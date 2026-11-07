from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestAnalyticDistributionRate(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env.user)
        cls.project_plan = cls.env.ref("analytic.analytic_plan_projects")
        cls.branch_a, cls.branch_b, cls.branch_c = cls.env[
            "account.analytic.account"
        ].create([
            {
                "name": "BS Rate Branch A",
                "plan_id": cls.project_plan.id,
                "company_id": cls.env.company.id,
            },
            {
                "name": "BS Rate Branch B",
                "plan_id": cls.project_plan.id,
                "company_id": cls.env.company.id,
            },
            {
                "name": "BS Rate Branch C",
                "plan_id": cls.project_plan.id,
                "company_id": cls.env.company.id,
            },
        ])
        cls.included_account = cls.env["account.account"].create({
            "name": "BS Included Analytic Rate Account",
            "code": "BSRATE001",
            "account_type": "expense",
            "bs_include_in_analytic_rate": True,
        })
        cls.excluded_account = cls.env["account.account"].create({
            "name": "BS Excluded Analytic Rate Account",
            "code": "BSRATE002",
            "account_type": "expense",
            "bs_include_in_analytic_rate": False,
        })
        cls.distribution_model = cls.env[
            "account.analytic.distribution.model"
        ].create({
            "company_id": cls.env.company.id,
            "bs_auto_compute_rate": True,
            "bs_compute_by": "month",
            "analytic_distribution": {
                str(cls.branch_a.id): 34,
                str(cls.branch_b.id): 33,
                str(cls.branch_c.id): 33,
            },
        })

    def _create_line(self, branch, amount, general_account=None, date=None):
        return self.env["account.analytic.line"].create({
            "name": "BS Analytic Rate Test Line",
            "date": date or self.today,
            "amount": amount,
            "account_id": branch.id,
            "general_account_id": (
                general_account or self.included_account
            ).id,
            "company_id": self.env.company.id,
        })

    def test_monthly_refresh_filters_accounts_and_dates(self):
        self._create_line(self.branch_a, -50)
        self._create_line(self.branch_b, -30)
        self._create_line(self.branch_c, -20)
        self._create_line(self.branch_a, -1000, self.excluded_account)
        self._create_line(
            self.branch_b,
            -1000,
            date=self.today - timedelta(days=30),
        )

        action = self.distribution_model.action_bs_refresh_rate()

        self.assertEqual(
            self.distribution_model.analytic_distribution,
            {
                str(self.branch_a.id): 50.0,
                str(self.branch_b.id): 30.0,
                str(self.branch_c.id): 20.0,
            },
        )
        self.assertEqual(action["tag"], "display_notification")

    def test_rounding_residual_is_added_to_last_distribution(self):
        percentages = self.distribution_model._bs_amounts_to_percentages({
            "a": 1,
            "b": 1,
            "c": 1,
        })
        self.assertEqual(sum(percentages.values()), 100.0)
        self.assertEqual(percentages["c"], 33.34)

    def test_rolling_period_boundaries(self):
        for compute_by, days in (
            ("day", 1),
            ("month", 30),
            ("quarter", 90),
            ("year", 365),
        ):
            self.distribution_model.bs_compute_by = compute_by
            date_from, date_to = self.distribution_model._bs_get_period_dates(
                date_to=self.today
            )
            self.assertEqual(date_to, self.today)
            self.assertEqual(date_from, self.today - timedelta(days=days - 1))

    def test_zero_total_sets_all_percentages_to_zero(self):
        self._create_line(self.branch_a, 100)
        self._create_line(self.branch_b, -100)

        self.distribution_model._bs_refresh_rate(date_to=self.today)

        self.assertEqual(
            self.distribution_model.analytic_distribution,
            {
                str(self.branch_a.id): 0.0,
                str(self.branch_b.id): 0.0,
                str(self.branch_c.id): 0.0,
            },
        )

    def test_cron_refreshes_only_enabled_models(self):
        self._create_line(self.branch_a, -60)
        self._create_line(self.branch_b, -40)
        disabled_model = self.distribution_model.copy({
            "bs_auto_compute_rate": False,
            "analytic_distribution": {
                str(self.branch_a.id): 10,
                str(self.branch_b.id): 90,
            },
        })

        self.env[
            "account.analytic.distribution.model"
        ]._cron_bs_refresh_rates()

        self.assertEqual(
            self.distribution_model.analytic_distribution,
            {
                str(self.branch_a.id): 60.0,
                str(self.branch_b.id): 40.0,
                str(self.branch_c.id): 0.0,
            },
        )
        self.assertEqual(
            disabled_model.analytic_distribution,
            {
                str(self.branch_a.id): 10.0,
                str(self.branch_b.id): 90.0,
            },
        )

    def test_settings_configure_real_cron_schedule(self):
        next_run = fields.Datetime.now() + timedelta(days=2)
        settings = self.env["res.config.settings"].create({
            "bs_analytic_rate_frequency": "weekly",
            "bs_analytic_rate_next_run": next_run,
        })
        settings.set_values()
        cron = self.env.ref(
            "bs_analytic_distribution_rate.ir_cron_refresh_analytic_rates"
        )
        self.assertTrue(cron.active)
        self.assertEqual(cron.interval_number, 1)
        self.assertEqual(cron.interval_type, "weeks")
        self.assertEqual(cron.nextcall, next_run)

        settings.bs_analytic_rate_frequency = "manual"
        settings.set_values()
        self.assertFalse(cron.active)
