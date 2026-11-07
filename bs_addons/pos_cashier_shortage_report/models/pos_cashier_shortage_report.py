from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class PosCashierShortageReport(models.Model):
    _name = "pos.cashier.shortage.report"
    _description = "POS Cashier Shortage Report"
    _order = "closing_date desc, session_id desc, payment_method_id"
    _rec_name = "session_id"

    session_id = fields.Many2one(
        "pos.session",
        string="Session",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
    )
    config_id = fields.Many2one(
        "pos.config", string="Point of Sale", required=True, readonly=True, index=True
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        readonly=True,
        index=True,
        ondelete="restrict",
    )
    closing_user_id = fields.Many2one(
        "res.users",
        string="Closing User",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
    )
    opening_date = fields.Datetime(
        string="Opening Date", required=True, readonly=True, index=True
    )
    closing_date = fields.Datetime(
        string="Closing Date", required=True, readonly=True, index=True
    )
    payment_method_id = fields.Many2one(
        "pos.payment.method",
        string="Payment Method",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
    )
    journal_id = fields.Many2one(
        "account.journal",
        string="Cash Journal",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        domain="[('type', '=', 'cash')]",
    )
    closing_expected = fields.Monetary(
        string="Closing Expected",
        required=True,
        readonly=True,
        currency_field="currency_id",
    )
    closing_counted = fields.Monetary(
        string="Closing Counted",
        required=True,
        readonly=True,
        currency_field="currency_id",
    )
    closing_difference = fields.Monetary(
        string="Closing Difference",
        required=True,
        readonly=True,
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        "res.currency", string="Currency", required=True, readonly=True
    )
    company_id = fields.Many2one(
        "res.company", string="Company", required=True, readonly=True, index=True
    )
    count_recorded = fields.Boolean(
        string="Count Recorded",
        required=True,
        readonly=True,
        help="Indicates whether the cashier explicitly entered a count for this cash method.",
    )

    _session_payment_method_unique = models.Constraint(
        "unique(session_id, payment_method_id)",
        "Only one cashier shortage line is allowed per session and cash payment method.",
    )

    @api.constrains("journal_id", "payment_method_id")
    def _check_cash_journal(self):
        for line in self:
            if (
                not line.payment_method_id.is_cash_count
                or line.payment_method_id.journal_id != line.journal_id
            ):
                raise ValidationError(
                    _(
                        "Cashier shortage lines must use the cash journal linked to the payment method."
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get("create_cashier_shortage_snapshot"):
            raise AccessError(
                _(
                    "Cashier shortage history can only be created when a POS session closes."
                )
            )
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get("create_cashier_shortage_snapshot"):
            raise AccessError(_("Cashier shortage history cannot be edited manually."))
        return super().write(vals)

    def unlink(self):
        if not self.env.is_superuser():
            raise AccessError(_("Cashier shortage history cannot be deleted."))
        return super().unlink()
