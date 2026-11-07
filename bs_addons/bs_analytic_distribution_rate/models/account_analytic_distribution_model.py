from datetime import timedelta

from odoo import _, api, fields, models
from odoo.tools.float_utils import float_round


class AccountAnalyticDistributionModel(models.Model):
    _inherit = "account.analytic.distribution.model"

    bs_auto_compute_rate = fields.Boolean(
        string="Auto Compute Distribution Rate",
        help=(
            "Recalculate this model's analytic percentages from analytic "
            "line amounts, either manually or by the scheduled action."
        ),
    )
    bs_compute_by = fields.Selection(
        selection=[
            ("day", "Day"),
            ("month", "Month"),
            ("quarter", "Quarter"),
            ("year", "Year"),
        ],
        string="Compute By",
        required=True,
        default="month",
    )

    _BS_PERIOD_DAYS = {
        "day": 1,
        "month": 30,
        "quarter": 90,
        "year": 365,
    }

    def _bs_get_period_dates(self, date_to=None):
        self.ensure_one()
        date_to = date_to or fields.Date.context_today(self)
        days = self._BS_PERIOD_DAYS[self.bs_compute_by]
        return date_to - timedelta(days=days - 1), date_to

    @api.model
    def _bs_distribution_account_ids(self, distribution_key):
        return [
            int(account_id)
            for account_id in str(distribution_key).split(",")
            if account_id.isdigit()
        ]

    def _bs_get_distribution_amounts(self, date_to=None):
        self.ensure_one()
        distribution = self.analytic_distribution or {}
        date_from, date_to = self._bs_get_period_dates(date_to=date_to)
        company = self.company_id or self.env.company
        analytic_line_model = self.env["account.analytic.line"]
        amounts = {}

        for distribution_key in distribution:
            account_ids = self._bs_distribution_account_ids(distribution_key)
            accounts = self.env["account.analytic.account"].browse(
                account_ids
            ).exists()
            if not account_ids or len(accounts) != len(account_ids):
                amounts[distribution_key] = 0.0
                continue

            domain = [
                ("date", ">=", date_from),
                ("date", "<=", date_to),
                ("company_id", "=", company.id),
                ("general_account_id.bs_include_in_analytic_rate", "=", True),
            ]
            for account in accounts:
                domain.append((account.root_plan_id._column_name(), "=", account.id))

            amount_group = analytic_line_model._read_group(
                domain=domain,
                aggregates=["amount:sum"],
            )
            amounts[distribution_key] = (
                (amount_group[0][0] or 0.0) if amount_group else 0.0
            )

        return amounts

    @api.model
    def _bs_amounts_to_percentages(self, amounts):
        if not amounts:
            return {}

        distribution_keys = list(amounts)
        total_amount = sum(amounts.values())
        if not total_amount:
            return {key: 0.0 for key in distribution_keys}

        precision = self.env["decimal.precision"].precision_get(
            "Percentage Analytic"
        )
        percentages = {
            key: float_round(
                amount / total_amount * 100.0,
                precision_digits=precision,
            )
            for key, amount in amounts.items()
        }
        last_key = distribution_keys[-1]
        residual = float_round(
            100.0 - sum(percentages.values()),
            precision_digits=precision,
        )
        percentages[last_key] = float_round(
            percentages[last_key] + residual,
            precision_digits=precision,
        )
        return percentages

    def _bs_refresh_rate(self, date_to=None):
        for distribution_model in self:
            amounts = distribution_model._bs_get_distribution_amounts(
                date_to=date_to
            )
            percentages = distribution_model._bs_amounts_to_percentages(amounts)
            distribution_model.analytic_distribution = percentages
        return True

    def action_bs_refresh_rate(self):
        self.ensure_one()
        self._bs_refresh_rate()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Analytic Distribution"),
                "message": _("The analytic distribution rates were refreshed."),
                "type": "success",
                "sticky": False,
            },
        }

    @api.model
    def _cron_bs_refresh_rates(self):
        distribution_models = self.sudo().search([
            ("bs_auto_compute_rate", "=", True),
        ])
        distribution_models._bs_refresh_rate()
