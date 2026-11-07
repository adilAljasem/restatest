from odoo import Command
from odoo.addons.account_reports.tests.common import TestAccountReportsCommon
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestProfitLossByCenter(TestAccountReportsCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids += cls.env.ref("analytic.group_analytic_accounting")
        cls.report = cls.env.ref(
            "bs_analytic_center_profit_loss.profit_loss_by_center"
        )

        plan = cls.env["account.analytic.plan"].create({"name": "Centers"})
        cls.profit_center_a, cls.profit_center_b = cls.env[
            "account.analytic.account"
        ].create([
            {
                "name": "Profit Center A",
                "plan_id": plan.id,
                "bs_center_type": "profit",
            },
            {
                "name": "Profit Center B",
                "plan_id": plan.id,
                "bs_center_type": "profit",
            },
        ])
        cls.revenue_center = cls.env["account.analytic.account"].create({
            "name": "Revenue Center",
            "plan_id": plan.id,
            "bs_center_type": "revenue",
        })
        cls.cost_center = cls.env["account.analytic.account"].create({
            "name": "Main Kitchen",
            "plan_id": plan.id,
            "bs_center_type": "cost",
        })

        revenue_account = cls.company_data["default_account_revenue"]
        revenue_account.bs_include_in_center_allocation = True
        excluded_revenue_account = revenue_account.copy({
            "name": "Revenue Excluded From Center Allocation",
            "bs_include_in_center_allocation": False,
        })
        expense_account = cls.company_data["default_account_expense"]
        counterpart_account = cls.company_data["default_account_payable"]

        move = cls.env["account.move"].create({
            "move_type": "entry",
            "date": "2026-09-01",
            "journal_id": cls.company_data["default_journal_misc"].id,
            "line_ids": [
                Command.create({
                    "name": "Profit Center A revenue",
                    "account_id": revenue_account.id,
                    "credit": 100.0,
                    "analytic_distribution": {str(cls.profit_center_a.id): 100.0},
                }),
                Command.create({
                    "name": "Profit Center B revenue",
                    "account_id": revenue_account.id,
                    "credit": 300.0,
                    "analytic_distribution": {str(cls.profit_center_b.id): 100.0},
                }),
                Command.create({
                    "name": "Revenue Center revenue",
                    "account_id": revenue_account.id,
                    "credit": 1091.0,
                    "analytic_distribution": {str(cls.revenue_center.id): 100.0},
                }),
                Command.create({
                    "name": "Revenue outside displayed centers",
                    "account_id": revenue_account.id,
                    "credit": 9.0,
                }),
                Command.create({
                    "name": "Excluded Profit Center A revenue",
                    "account_id": excluded_revenue_account.id,
                    "credit": 400.0,
                    "analytic_distribution": {str(cls.profit_center_a.id): 100.0},
                }),
                Command.create({
                    "name": "Excluded Profit Center B revenue",
                    "account_id": excluded_revenue_account.id,
                    "credit": 100.0,
                    "analytic_distribution": {str(cls.profit_center_b.id): 100.0},
                }),
                Command.create({
                    "name": "Cost Center expense",
                    "account_id": expense_account.id,
                    "debit": 3000.0,
                    "analytic_distribution": {str(cls.cost_center.id): 100.0},
                }),
                Command.create({
                    "name": "Counterpart",
                    "account_id": counterpart_account.id,
                    "credit": 1000.0,
                }),
            ],
        })
        move.action_post()

    def test_center_columns_and_cost_allocation(self):
        options = self._generate_options(
            self.report,
            "2026-09-01",
            "2026-09-30",
        )
        self.assertEqual(
            [header["name"] for header in options["column_headers"][-1]],
            ["Profit Center A", "Profit Center B", "Revenue Center", "Total"],
        )

        lines = self.report._get_lines(options)
        revenue_line = next(line for line in lines if line["name"] == "Revenue")
        cost_center_line = next(line for line in lines if line["name"] == "Main Kitchen")
        final_net_profit_line = next(
            line
            for line in lines
            if line["name"] == "Net Profit Left After Cost Center"
        )

        self.assertEqual(
            [column["no_format"] for column in revenue_line["columns"]],
            [500.0, 400.0, 1091.0, 2000.0],
        )
        cost_values = [column["no_format"] for column in cost_center_line["columns"]]
        self.assertAlmostEqual(cost_values[0], 100.0 / 400.0 * -3000.0)
        self.assertAlmostEqual(cost_values[1], 300.0 / 400.0 * -3000.0)
        self.assertEqual(cost_values[2], 0.0)
        self.assertEqual(cost_values[3], -3000.0)
        final_values = [
            column["no_format"]
            for column in final_net_profit_line["columns"]
        ]
        self.assertAlmostEqual(final_values[0], 500.0 + cost_values[0])
        self.assertAlmostEqual(final_values[1], 400.0 + cost_values[1])
        self.assertEqual(final_values[2], 1091.0)
        self.assertEqual(final_values[3], -4000.0)
        self.assertEqual(lines[-2]["name"], "Main Kitchen")
        self.assertEqual(lines[-1]["name"], "Net Profit Left After Cost Center")
