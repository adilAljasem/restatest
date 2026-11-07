from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_always_to_invoice = fields.Boolean(
        related="pos_config_id.always_to_invoice",
        readonly=False,
    )
    pos_disable_auto_invoice_pdf_download = fields.Boolean(
        related="pos_config_id.disable_auto_invoice_pdf_download",
        readonly=False,
    )
    pos_default_customer_id = fields.Many2one(
        comodel_name="res.partner",
        related="pos_config_id.default_customer_id",
        readonly=False,
        check_company=True,
        domain="[('company_id', 'in', [False, company_id])]",
    )
