import logging

from lxml import etree

from odoo import fields, models

_logger = logging.getLogger(__name__)

# PIH used by ZATCA for the first invoice of a chain (base64 of sha256("0")),
# identical to the constant used by l10n_sa_edi.
ZATCA_INITIAL_PIH = "NWZlY2ViNjZmZmM4NmYzOGQ5NTI3ODZjNmQ2OTZjNzljMmRiYzIzOWRkNGU5MWI0NjcyOWQ3M2EyN2ZiNTdlOQ=="


class AccountMove(models.Model):
    _inherit = 'account.move'

    l10n_sa_async_signed = fields.Boolean(
        string="ZATCA signed at POS", copy=False, readonly=True,
        help="Technical: the invoice was signed and chained locally at checkout and is reported to ZATCA in the background.",
    )
    l10n_sa_async_pih = fields.Char(
        string="ZATCA Previous Invoice Hash (frozen)", copy=False, readonly=True,
        help="Technical: PIH frozen at signing time, so the XML reported later is identical to the one signed at checkout.",
    )

    def _l10n_sa_async_eligible(self):
        """Only simplified (B2C) invoices may be reported after the fact.
        Standard (B2B) invoices need real-time clearance and keep the standard flow."""
        self.ensure_one()
        return (
            self.country_code == 'SA'
            and self.state == 'posted'
            and self.move_type in ('out_invoice', 'out_refund')
            and self._l10n_sa_is_simplified()
        )

    def _l10n_sa_lock_journal(self, nowait=False):
        """Serialize chain *signing* per journal: the order of PIH values must match the ICV order.
        Only used when signing (checkout, or re-signing a rejected invoice), never when reporting."""
        self.ensure_one()
        # FOR NO KEY UPDATE, not FOR UPDATE: it still serializes the signers (it conflicts with itself),
        # but it does not conflict with the FOR KEY SHARE lock PostgreSQL takes on the journal row for
        # every INSERT of an account.move / account.move.line referencing it. With FOR UPDATE, a second
        # cashier (or Blend order, or backend invoice) on the same journal was blocked as soon as its
        # invoice was *created*, and waited for the whole remaining request of the first cashier.
        self.env.cr.execute(
            "SELECT id FROM account_journal WHERE id = %s FOR NO KEY UPDATE" + (" NOWAIT" if nowait else ""),
            [self.journal_id.id],
        )
        self.journal_id.invalidate_recordset(['l10n_sa_latest_submission_hash'])

    def _l10n_sa_async_sign(self, nowait=False):
        """Do locally everything ZATCA needs from the device (EGS), with no network call:
        ICV (chain index), UUID, PIH, invoice hash, digital signature -> Phase 2 QR code.
        The journal's latest hash is advanced immediately so the next invoice chains onto this one.

        :return: True if signed, False if the XML could not be generated (left to the standard flow).
        """
        self.ensure_one()
        journal = self.journal_id.sudo()
        self._l10n_sa_lock_journal(nowait=nowait)

        if self.company_id.l10n_sa_api_mode == 'sandbox' or not journal.l10n_sa_latest_submission_hash:
            pih = ZATCA_INITIAL_PIH
        else:
            pih = journal.l10n_sa_latest_submission_hash

        self.sudo().write({
            'l10n_sa_async_signed': True,
            'l10n_sa_async_pih': pih,
            'l10n_sa_chain_index': journal._l10n_sa_edi_get_next_chain_index(),
        })
        xml_content = self.sudo()._l10n_sa_generate_unsigned_data()
        if not isinstance(xml_content, str):
            # UBL generation error (missing data...): let the standard flow report the error.
            _logger.warning("ZATCA async: could not generate XML for %s, falling back to standard flow", self.name)
            self.sudo().write({'l10n_sa_async_signed': False, 'l10n_sa_async_pih': False, 'l10n_sa_chain_index': 0})
            return False

        journal.l10n_sa_latest_submission_hash = self._l10n_sa_async_invoice_hash(xml_content)
        # QR depends on signature/chain index; recompute now so the POS receipt gets it immediately.
        self.invalidate_recordset(['l10n_sa_qr_code_str'])
        return True

    def _l10n_sa_async_invoice_hash(self, unsigned_xml):
        """Hash that standard Odoo stores as next PIH after a successful submission, i.e. the hash of the
        *signed* document. Signing (l10n_sa_edi `_l10n_sa_sign_xml`) only re-indents the XML and fills the
        UBLExtensions/QR, which the ZATCA hash transform excludes, so it can be computed before signing."""
        root = etree.fromstring(unsigned_xml.encode() if isinstance(unsigned_xml, str) else unsigned_xml)
        etree.indent(root, space='    ')
        invoice_hash = self.env['account.edi.xml.ubl_21.zatca']._l10n_sa_generate_invoice_xml_hash(etree.tostring(root))
        return invoice_hash.decode() if isinstance(invoice_hash, bytes) else invoice_hash

    def _l10n_sa_log_results(self, xml_content, response_data=None, error=False):
        """For locally-chained invoices the journal hash was already advanced at signing time.
        Reporting happens later, so the standard code must not move the hash back to an older invoice.
        The journal row is not touched at all here: reporting runs in the background while cashiers keep
        signing new invoices on the same journal, and locking/writing that row from both sides caused
        "could not serialize access due to concurrent update" / deadlocks (after ZATCA had already accepted)."""
        if not self.l10n_sa_async_signed:
            return super()._l10n_sa_log_results(xml_content, response_data=response_data, error=error)
        return super(AccountMove, self.with_context(l10n_sa_async_keep_chain_head=True))._l10n_sa_log_results(
            xml_content, response_data=response_data, error=error)
