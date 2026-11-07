from lxml import etree

from psycopg2 import errors as pg_errors

from odoo import _, models

PG_CONCURRENCY_EXCEPTIONS = (pg_errors.LockNotAvailable, pg_errors.SerializationFailure, pg_errors.DeadlockDetected)


class AccountEdiFormat(models.Model):
    _inherit = 'account.edi.format'

    def _l10n_sa_generate_zatca_template(self, invoice):
        """Use the PIH frozen at checkout, so the XML regenerated at reporting time (and for the QR code)
        is byte-for-byte the one that was hashed and signed, even if newer invoices were chained since."""
        xml_content = super()._l10n_sa_generate_zatca_template(invoice)
        if isinstance(xml_content, str) and invoice.l10n_sa_async_pih:
            root = etree.fromstring(xml_content)
            nodes = root.xpath('//*[local-name()="ID"][text()="PIH"]/following-sibling::*/*')
            if nodes and nodes[0].text != invoice.l10n_sa_async_pih:
                nodes[0].text = invoice.l10n_sa_async_pih
                xml_content = etree.tostring(root, with_tail=False).decode()
        return xml_content

    def _l10n_sa_post_zatca_edi(self, invoice):
        if invoice.l10n_sa_async_signed:
            if not invoice.l10n_sa_chain_index:
                # Previously rejected by ZATCA and fixed by the user: re-sign it at the end of the chain.
                try:
                    with self.env.cr.savepoint():
                        signed = invoice._l10n_sa_async_sign(nowait=True)
                except PG_CONCURRENCY_EXCEPTIONS:
                    # a cashier is signing on this journal right now: retry on the next run
                    self.env.invalidate_all()
                    return {invoice: {
                        'error': _("Journal busy, the invoice will be re-submitted automatically."),
                        'blocking_level': 'info',
                    }}
                if not signed:
                    return super()._l10n_sa_post_zatca_edi(invoice)
            # Tell the chain-integrity check which invoice is being reported (see account_journal.py).
            ctx = {'l10n_sa_async_move_id': invoice.id}
            return super(AccountEdiFormat, self.with_context(**ctx))._l10n_sa_post_zatca_edi(invoice.with_context(**ctx))
        return super()._l10n_sa_post_zatca_edi(invoice)
