from odoo import api, models


class AccountMoveSend(models.AbstractModel):
    _inherit = 'account.move.send'

    @api.model
    def _get_default_sending_methods(self, move):
        if move.pos_deferred_pdf_requested and not move.invoice_pdf_report_id:
            return set()
        return super()._get_default_sending_methods(move)

    @api.model
    def _get_default_extra_edis(self, move):
        if move.pos_deferred_pdf_requested and not move.invoice_pdf_report_id:
            return set()
        return super()._get_default_extra_edis(move)

    @api.model
    def _get_default_invoice_edi_format(self, move, **kwargs):
        if move.pos_deferred_pdf_requested and not move.invoice_pdf_report_id:
            return False
        return super()._get_default_invoice_edi_format(move, **kwargs)
