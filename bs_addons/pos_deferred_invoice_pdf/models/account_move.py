from odoo import _, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = 'account.move'

    pos_deferred_pdf_requested = fields.Boolean(copy=False, readonly=True)

    def _check_pos_deferred_pdf_ready(self):
        pending = self.filtered(lambda move: move.pos_deferred_pdf_requested and not move.invoice_pdf_report_id)
        if pending:
            if any(move.sending_data for move in pending):
                raise UserError(_('The invoice PDF is being prepared. Please try again shortly.'))
            raise UserError(_('The invoice PDF could not be prepared. Please contact an administrator to retry it.'))

    def action_invoice_download_pdf(self, target='download'):
        self._check_pos_deferred_pdf_ready()
        return super().action_invoice_download_pdf(target=target)

    def _get_invoice_legal_documents(self, filetype, allow_fallback=False):
        if filetype == 'pdf' and allow_fallback:
            self._check_pos_deferred_pdf_ready()
        return super()._get_invoice_legal_documents(filetype, allow_fallback=allow_fallback)

    def _get_invoice_legal_documents_all(self, allow_fallback=False):
        if allow_fallback:
            self._check_pos_deferred_pdf_ready()
        return super()._get_invoice_legal_documents_all(allow_fallback=allow_fallback)
