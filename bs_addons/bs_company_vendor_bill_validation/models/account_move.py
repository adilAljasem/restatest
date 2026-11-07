from odoo import _, api, models
from odoo.exceptions import UserError, ValidationError


class AccountMove(models.Model):
    _inherit = "account.move"

    def action_post(self):
        vendor_bills_without_payment_terms = self.filtered(
            lambda move: move.state == "draft"
            and move.move_type == "in_invoice"
            and not move.invoice_payment_term_id
        )
        if vendor_bills_without_payment_terms:
            raise UserError(
                _("Payment Terms are required before confirming a Vendor Bill.")
            )
        return super().action_post()


