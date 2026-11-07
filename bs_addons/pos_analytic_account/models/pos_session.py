from odoo import models


class PosSession(models.Model):
    _inherit = "pos.session"

    def _post_statement_difference(self, amount):
        self.ensure_one()
        existing_statement_line_ids = self.statement_line_ids.ids
        result = super()._post_statement_difference(amount)

        analytic_account = self.config_id.analytic_account_id
        cash_journal = self.cash_journal_id
        if not amount or not analytic_account or cash_journal.type != "cash":
            return result

        difference_account = (
            cash_journal.loss_account_id
            if amount < 0
            else cash_journal.profit_account_id
        )
        if not difference_account:
            return result

        statement_lines = self.env["account.bank.statement.line"].search(
            [
                ("pos_session_id", "=", self.id),
                ("journal_id", "=", cash_journal.id),
                ("id", "not in", existing_statement_line_ids),
            ]
        )
        difference_lines = statement_lines.move_id.line_ids.filtered(
            lambda line: line.account_id == difference_account
            and not line.tax_line_id
            and line.display_type != "tax"
        )
        for line in difference_lines:
            distribution = line._merge_pos_analytic_distribution(analytic_account)
            if distribution != (line.analytic_distribution or {}):
                line.analytic_distribution = distribution

        return result

    def _get_diff_vals(
        self,
        payment_method_id,
        diff_amount,
        outstanding_account=False,
    ):
        values = super()._get_diff_vals(
            payment_method_id,
            diff_amount,
            outstanding_account=outstanding_account,
        )
        if not values or not self.config_id.analytic_account_id:
            return values

        analytic_account = self.config_id.analytic_account_id
        for line_values in values:
            line = self.env["account.move.line"].new(line_values)
            if line._is_pos_analytic_eligible():
                line_values["analytic_distribution"] = (
                    line._merge_pos_analytic_distribution(analytic_account)
                )
        return values

    def _create_account_move(
        self,
        balancing_account=False,
        amount_to_balance=0,
        bank_payment_method_diffs=None,
    ):
        data = super()._create_account_move(
            balancing_account=balancing_account,
            amount_to_balance=amount_to_balance,
            bank_payment_method_diffs=bank_payment_method_diffs,
        )
        for session in self:
            session.move_id.line_ids._apply_pos_analytic_account(
                session.config_id.analytic_account_id
            )
        return data
