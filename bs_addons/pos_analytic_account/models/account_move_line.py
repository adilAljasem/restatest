from odoo import models


PROFIT_AND_LOSS_ACCOUNT_PREFIXES = ("income", "expense")


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    def _is_pos_analytic_eligible(self):
        self.ensure_one()
        return bool(
            self.account_id
            and self.account_id.account_type.startswith(
                PROFIT_AND_LOSS_ACCOUNT_PREFIXES
            )
            and not self.tax_line_id
            and self.display_type not in ("tax", "payment_term")
        )

    def _merge_pos_analytic_distribution(self, analytic_account):
        self.ensure_one()
        distribution = dict(self.analytic_distribution or {})
        if not analytic_account:
            return distribution

        account_ids = set()
        for key in distribution:
            try:
                account_ids.update(int(account_id) for account_id in str(key).split(","))
            except ValueError:
                continue
        analytic_accounts = self.env["account.analytic.account"].browse(account_ids)
        account_by_id = {account.id: account for account in analytic_accounts.exists()}

        merged_distribution = {}
        for key, percentage in distribution.items():
            try:
                key_account_ids = [
                    int(account_id) for account_id in str(key).split(",")
                ]
            except ValueError:
                merged_distribution[str(key)] = percentage
                continue

            remaining_account_ids = [
                account_id
                for account_id in key_account_ids
                if account_id not in account_by_id
                or account_by_id[account_id].plan_id != analytic_account.plan_id
            ]
            if remaining_account_ids:
                remaining_key = ",".join(map(str, remaining_account_ids))
                merged_distribution[remaining_key] = (
                    merged_distribution.get(remaining_key, 0.0) + percentage
                )

        merged_distribution[str(analytic_account.id)] = 100.0
        return merged_distribution

    def _apply_pos_analytic_account(self, analytic_account):
        if not analytic_account:
            return self
        eligible_lines = self.filtered(
            lambda line: line._is_pos_analytic_eligible()
        )
        for line in eligible_lines:
            distribution = line._merge_pos_analytic_distribution(analytic_account)
            if distribution != (line.analytic_distribution or {}):
                line.analytic_distribution = distribution
        return eligible_lines
