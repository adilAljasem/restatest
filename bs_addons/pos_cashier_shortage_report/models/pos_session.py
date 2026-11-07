import math

from odoo import _, fields, models
from odoo.exceptions import UserError


class PosSession(models.Model):
    _inherit = "pos.session"

    cashier_cash_count_data = fields.Json(
        string="Pending Cash Counts", readonly=True, copy=False
    )
    cashier_closing_employee_id = fields.Many2one(
        "hr.employee", string="Closing Employee", readonly=True, copy=False
    )
    cashier_closing_user_id = fields.Many2one(
        "res.users", string="Closing User", readonly=True, copy=False
    )
    cashier_shortage_report_ids = fields.One2many(
        "pos.cashier.shortage.report",
        "session_id",
        string="Cashier Shortage Lines",
        readonly=True,
    )

    def store_cashier_cash_counts(self, cash_count_pairs):
        """Persist the closing popup inputs until the session closes successfully."""
        self.ensure_one()
        if self.user_id != self.env.user and not self.env.user.has_group(
            "point_of_sale.group_pos_manager"
        ):
            raise UserError(
                _("Only the session user or a POS manager can submit cash counts.")
            )
        if self.state == "closed":
            raise UserError(_("This POS session is already closed."))

        submitted_counts = {}
        for payment_method_id, counted in cash_count_pairs or []:
            try:
                counted = float(counted)
            except (TypeError, ValueError):
                raise UserError(_("Every cash count must be a valid number.")) from None
            if not math.isfinite(counted):
                raise UserError(_("Every cash count must be a finite number."))
            submitted_counts[int(payment_method_id)] = counted

        cash_methods = self.payment_method_ids.filtered("is_cash_count")
        unknown_method_ids = set(submitted_counts) - set(cash_methods.ids)
        if unknown_method_ids:
            raise UserError(
                _("Only cash payment methods from this POS session can be counted.")
            )

        self.sudo().write(
            {
                "cashier_cash_count_data": {
                    str(method.id): submitted_counts[method.id]
                    for method in cash_methods
                    if method.id in submitted_counts
                },
                "cashier_closing_employee_id": self.employee_id.id,
                "cashier_closing_user_id": self.env.user.id,
            }
        )
        return True

    def _prepare_cashier_shortage_snapshot_vals(self):
        self.ensure_one()
        cash_methods = self.payment_method_ids.filtered("is_cash_count")
        if not cash_methods:
            return []

        orders = self._get_closed_orders()
        payments = orders.payment_ids.filtered(
            lambda payment: payment.payment_method_id in cash_methods
        )
        payments_by_method = {
            method: sum(method_payments.mapped("amount"))
            for method, method_payments in payments.grouped("payment_method_id").items()
        }
        default_cash_method = cash_methods[:1]
        submitted_counts = {
            int(payment_method_id): float(counted)
            for payment_method_id, counted in (
                self.cashier_cash_count_data or {}
            ).items()
        }
        employee = (
            self.cashier_closing_employee_id
            or self.employee_id
            or self.user_id.employee_id
        )
        closing_user = self.cashier_closing_user_id or self.env.user
        closing_date = self.stop_at or fields.Datetime.now()

        values = []
        for payment_method in cash_methods:
            payment_amount = payments_by_method.get(payment_method, 0.0)
            if payment_method == default_cash_method:
                expected = (
                    self.cash_register_balance_start
                    + payment_amount
                    + sum(self.sudo().statement_line_ids.mapped("amount"))
                )
                counted = submitted_counts.get(
                    payment_method.id, self.cash_register_balance_end_real
                )
                count_recorded = (
                    payment_method.id in submitted_counts
                    or self.cash_register_balance_end_real != 0.0
                )
            else:
                expected = payment_amount
                count_recorded = payment_method.id in submitted_counts
                # Manual backend closing has no input for secondary cash methods.
                # Avoid manufacturing a false shortage while marking that no count was entered.
                counted = submitted_counts.get(payment_method.id, expected)

            expected = self.currency_id.round(expected)
            counted = self.currency_id.round(counted)
            values.append(
                {
                    "session_id": self.id,
                    "config_id": self.config_id.id,
                    "employee_id": employee.id,
                    "closing_user_id": closing_user.id,
                    "opening_date": self.start_at or closing_date,
                    "closing_date": closing_date,
                    "payment_method_id": payment_method.id,
                    "journal_id": payment_method.journal_id.id,
                    "closing_expected": expected,
                    "closing_counted": counted,
                    "closing_difference": self.currency_id.round(counted - expected),
                    "currency_id": self.currency_id.id,
                    "company_id": self.company_id.id,
                    "count_recorded": count_recorded,
                }
            )
        return values

    def _save_cashier_shortage_snapshot(self, values):
        self.ensure_one()
        report_model = (
            self.env["pos.cashier.shortage.report"]
            .sudo()
            .with_context(create_cashier_shortage_snapshot=True)
        )
        for vals in values:
            existing = report_model.search(
                [
                    ("session_id", "=", vals["session_id"]),
                    ("payment_method_id", "=", vals["payment_method_id"]),
                ],
                limit=1,
            )
            if existing:
                existing.write(vals)
            else:
                report_model.create(vals)

    def _validate_session(
        self,
        balancing_account=False,
        amount_to_balance=0,
        bank_payment_method_diffs=None,
    ):
        snapshots_by_session = {
            session.id: session._prepare_cashier_shortage_snapshot_vals()
            for session in self
        }
        result = super()._validate_session(
            balancing_account=balancing_account,
            amount_to_balance=amount_to_balance,
            bank_payment_method_diffs=bank_payment_method_diffs,
        )
        for session in self.filtered(lambda current: current.state == "closed"):
            session._save_cashier_shortage_snapshot(
                snapshots_by_session.get(session.id, [])
            )
            session.sudo().cashier_cash_count_data = False
        return result
