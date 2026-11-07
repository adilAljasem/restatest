from odoo import fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    always_to_invoice = fields.Boolean(
        string="Always To Invoice",
        help="Automatically invoice every order from this point of sale and prevent cashiers from disabling invoicing.",
    )
    disable_auto_invoice_pdf_download = fields.Boolean(
        string="Disable Automatic Invoice PDF Download",
        help="Do not automatically download or open the invoice PDF after validating a payment.",
    )
    default_customer_id = fields.Many2one(
        comodel_name="res.partner",
        string="Default Customer",
        check_company=True,
        domain="[('company_id', 'in', [False, company_id])]",
        help="Customer selected automatically on each new order. Cashiers can still select another customer.",
    )
