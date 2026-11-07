from odoo import fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    l10n_sa_zatca_async = fields.Boolean(
        string="Report ZATCA in background",
        default=True,
        help="Simplified (B2C) invoices are signed and given their QR code instantly at checkout, "
             "then reported to ZATCA in the background (ZATCA allows reporting within 24 hours). "
             "Standard (B2B) invoices still go through real-time clearance.",
    )
