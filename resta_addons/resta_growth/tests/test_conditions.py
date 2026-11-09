from datetime import timedelta

from odoo.tests import tagged

from .common import GrowthCase


@tagged("post_install", "-at_install", "resta_growth")
class TestConditions(GrowthCase):

    def test_daily_recording_met(self):
        self.make_days(35)
        res = self.status("daily_recording")
        self.assertTrue(res["met"])
        self.assertEqual(res["current_value"], 35)

    def test_daily_recording_holidays_do_not_count(self):
        # every Friday is a declared day off and was not recorded: coverage stays 100%
        self.profile.sudo().off_fri = True
        self.make_days(42, planned=lambda d: d.weekday() != 4, recorded=lambda d: d.weekday() != 4)
        res = self.status("daily_recording")
        self.assertEqual(res["details"]["coverage"], 100.0)
        self.assertTrue(res["met"])

    def test_daily_recording_low_coverage(self):
        self.make_days(40, recorded=lambda d: d.day % 4 != 0)  # ~25% of days missing
        res = self.status("daily_recording")
        self.assertFalse(res["met"])
        self.assertLess(res["details"]["coverage"], 95)

    def test_zatca(self):
        self.make_days(30)
        self.assertTrue(self.status("zatca_compliance")["met"])

    def test_zatca_pending_blocks(self):
        self.make_days(30, invoices_pending_48h=lambda d: 1 if d == self.today - timedelta(days=3) else 0)
        res = self.status("zatca_compliance")
        self.assertFalse(res["met"])
        self.assertEqual(res["details"]["pending"], 1)

    def test_zatca_no_invoices(self):
        self.make_days(30, invoices_total=0, invoices_sent=0)
        self.assertFalse(self.status("zatca_compliance")["met"])

    def test_weekly_inventory_streak(self):
        sundays = lambda d: d.weekday() == 6
        self.make_days(60, inventory_count=sundays, inventory_coverage=lambda d: 90.0 if d.weekday() == 6 else 0)
        res = self.status("weekly_inventory")
        self.assertGreaterEqual(res["details"]["streak"], 6)
        self.assertTrue(res["met"])

    def test_weekly_inventory_low_coverage_breaks_streak(self):
        self.make_days(60, inventory_count=lambda d: d.weekday() == 6,
                       inventory_coverage=lambda d: 50.0 if d.weekday() == 6 else 0)
        self.assertFalse(self.status("weekly_inventory")["met"])

    def test_waste_ratio(self):
        self.make_days(42)  # 30 / 1300 = 2.3 %
        self.assertTrue(self.status("waste_ratio")["met"])
        self.env["resta.growth.daily"].sudo().search([]).write({"waste_value": 100.0})  # 7.7 %
        self.assertFalse(self.status("waste_ratio")["met"])

    def test_cash_accuracy(self):
        self.make_days(42, sessions_cash_ok=lambda d: 0 if d.day % 15 == 0 else 1)
        res = self.status("cash_accuracy")
        self.assertTrue(res["met"], res)
        self.env["resta.growth.daily"].sudo().search([]).write({"sessions_cash_ok": 0})
        self.assertFalse(self.status("cash_accuracy")["met"])

    def test_menu_complete(self):
        self.make_menu(15)
        self.assertTrue(self.status("menu_complete")["met"])

    def test_menu_incomplete(self):
        self.make_menu(15, complete=False)
        res = self.status("menu_complete")
        self.assertFalse(res["met"])
        self.assertTrue(res["details"]["incomplete"])

    def test_daily_aggregation_runs(self):
        """The SQL aggregation runs on a real database without error and creates one row per day."""
        Daily = self.env["resta.growth.daily"]
        Daily._compute_range(self.company, self.today - timedelta(days=9), self.today)
        self.assertEqual(Daily.search_count([("company_id", "=", self.company.id)]), 10)
