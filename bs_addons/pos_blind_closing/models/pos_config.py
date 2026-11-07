from odoo import fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    blind_closing = fields.Boolean(
        string="Blind Closing",
        default=True,
        help="Hide every expected amount (sales, cash, card totals, differences) in the "
        "closing popup. The cashier enters what was actually counted and the "
        "differences are calculated on the server.",
    )
    blind_closing_manager_visible = fields.Boolean(
        string="Show Amounts to POS Managers",
        default=False,
        help="Users in the Point of Sale / Administrator group see the standard closing popup.",
    )
    blind_closing_empty_inputs = fields.Boolean(
        string="Force Cashier Entry",
        default=True,
        help="All count fields start empty and the register cannot be closed until every field is filled.",
    )
    blind_closing_show_all_banks = fields.Boolean(
        string="Show All Bank Methods",
        default=True,
        help="Show a count field for every bank payment method, even if it had no sales, "
        "so the cashier cannot tell which methods were used.",
    )
    blind_closing_hide_reports = fields.Boolean(
        string="Hide Sales Reports",
        default=True,
        help="Hide the Daily Sale and Print Sales Details buttons in the closing popup.",
    )
    blind_closing_confirm = fields.Boolean(
        string="Confirm Before Closing",
        default=True,
        help="Show a confirmation with the entered amounts before closing the register.",
    )
    blind_closing_log = fields.Boolean(
        string="Log Counts in Session",
        default=True,
        help="Post expected, counted and difference per payment method in the session chatter (back-end only).",
    )

    def _get_blind_closing_options(self):
        self.ensure_one()
        return {
            "empty_inputs": self.blind_closing_empty_inputs,
            "show_all_banks": self.blind_closing_show_all_banks,
            "hide_reports": self.blind_closing_hide_reports,
            "confirm": self.blind_closing_confirm,
        }
