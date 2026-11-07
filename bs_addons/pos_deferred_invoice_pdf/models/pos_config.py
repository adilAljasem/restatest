from odoo import fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    defer_invoice_pdf_generation = fields.Boolean(
        string='Generate Invoice PDF After Payment',
        help='Post and reconcile the invoice during payment, then generate its PDF in the background.',
    )
