import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class AccountEdiDocument(models.Model):
    _inherit = 'account.edi.document'

    def _process_documents_web_services(self, job_count=None, with_commit=True):
        """At POS checkout (context l10n_sa_pos_async): sign simplified ZATCA invoices locally instead of
        calling the ZATCA API, then wake up the EDI cron which reports them in the background."""
        if not self.env.context.get('l10n_sa_pos_async'):
            return super()._process_documents_web_services(job_count=job_count, with_commit=with_commit)

        async_docs = self.filtered(
            lambda d: d.edi_format_id.code == 'sa_zatca'
            and d.state == 'to_send'
            and d.move_id._l10n_sa_async_eligible()
        )
        signed = self.browse()
        for doc in async_docs:
            move = doc.move_id
            if move.l10n_sa_chain_index or move._l10n_sa_async_sign():
                signed |= doc
        if signed:
            self.env.ref('account_edi.ir_cron_edi_network')._trigger()

        remaining = self - signed  # B2B (clearance) and fall-backs keep the standard synchronous flow
        if not remaining:
            return 0
        return super(AccountEdiDocument, remaining)._process_documents_web_services(
            job_count=job_count, with_commit=with_commit)

    def _prepare_jobs(self):
        """Report ZATCA invoices in chain (ICV) order."""
        jobs = super()._prepare_jobs()

        def chain_key(job):
            indexes = [
                d.move_id.l10n_sa_chain_index for d in job['documents']
                if d.edi_format_id.code == 'sa_zatca' and d.state == 'to_send' and d.move_id.l10n_sa_chain_index
            ]
            return (0, min(indexes)) if indexes else (1, 0)

        return sorted(jobs, key=chain_key)

    @api.model
    def _l10n_sa_cron_report_pos_async(self):
        """Safety net every 5 minutes: if POS invoices are still waiting (e.g. after an internet outage),
        wake up the standard EDI cron. Reporting itself is only ever done by that single cron job, so two
        jobs never submit the same invoices concurrently."""
        pending = self.search_count([
            ('state', '=', 'to_send'),
            ('edi_format_id.code', '=', 'sa_zatca'),
            ('move_id.state', '=', 'posted'),
            ('move_id.l10n_sa_async_signed', '=', True),
            ('blocking_level', '!=', 'error'),
        ], limit=1)
        if pending:
            cron = self.env.ref('account_edi.ir_cron_edi_network').sudo()
            if not cron.active:
                cron.active = True
            cron._trigger()
