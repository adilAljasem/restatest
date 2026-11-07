"""Database integration checks, run by Odoo with --test-enable after upgrade."""
from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..models.preparation_summary import ENABLED_PARAMETER


@tagged("post_install", "-at_install")
class TestPreparationSummarySettings(TransactionCase):
    def test_report_backing_table_exists_and_stays_empty(self):
        self.env.cr.execute("SELECT to_regclass('stock_request_preparation_summary')")
        self.assertIsNotNone(self.env.cr.fetchone()[0])
        self.env.cr.execute("SELECT COUNT(*) FROM stock_request_preparation_summary")
        self.assertEqual(self.env.cr.fetchone()[0], 0)
        report = self.env["stock.request.preparation.summary"]
        with self.assertRaises(AccessError):
            report.create({"code": "must-not-be-stored"})

    def test_pdf_action_uses_odoo19_report_groups(self):
        action = self.env.ref("stock_request.preparation_summary_pdf_action")
        self.assertIn(
            self.env.ref("stock_request.group_stock_request_warehouse_responsible"),
            action.group_ids,
        )

    def test_setting_survives_other_settings_and_upgrade_menu_sync(self):
        Settings = self.env["res.config.settings"]
        settings = Settings.create({"stock_request_preparation_summary_enabled": False})
        settings.set_values()
        self.assertEqual(self.env["ir.config_parameter"].sudo().get_param(ENABLED_PARAMETER), "False")
        self.assertFalse(self.env.ref("stock_request.preparation_summary_menu").active)
        # A fresh settings form must retain the disabled value, even when the
        # user changes another setting and saves it.
        next_settings = Settings.create({"stock_request_transfer_flow": "two_step_no_transit"})
        self.assertFalse(next_settings.stock_request_preparation_summary_enabled)
        next_settings.set_values()
        Settings._sync_preparation_summary_menu()
        self.assertFalse(self.env.ref("stock_request.preparation_summary_menu").active)
        next_settings.stock_request_preparation_summary_enabled = True
        next_settings.set_values()
        self.assertTrue(self.env.ref("stock_request.preparation_summary_menu").active)

    def test_disabled_report_blocks_existing_sessions_and_exports(self):
        self.env["ir.config_parameter"].sudo().set_param(ENABLED_PARAMETER, "False")
        report = self.env["stock.request.preparation.summary"]
        for call in (
            lambda: report.web_search_read([], {"quantity": {}}),
            lambda: report.web_read_group([], ["company_id"]),
            lambda: report.open_orders([1, 2, 3, 4], []),
            lambda: report.action_print_pdf([]),
            lambda: report.export_xlsx([]),
            lambda: self.env["report.stock_request.preparation_summary_pdf"]._get_report_values([], {}),
        ):
            with self.assertRaises(AccessError):
                call()

    def test_native_view_metadata_and_empty_search(self):
        self.env.user.group_ids = [Command.link(self.env.ref("stock_request.group_stock_request_manager").id)]
        self.env["ir.config_parameter"].sudo().set_param(ENABLED_PARAMETER, "True")
        report = self.env["stock.request.preparation.summary"]
        fields = report.fields_get()
        self.assertTrue(fields["company_id"]["groupable"])
        self.assertFalse(fields["warehouse_id"]["groupable"])
        self.assertFalse(fields["quantity"]["searchable"])
        self.assertEqual(report.web_search_read([("product_id", "=", 0)], {"quantity": {}}),
                         {"length": 0, "records": []})
        self.assertEqual(report.web_read_group([("product_id", "=", 0)], ["company_id"]),
                         {"length": 0, "groups": []})
