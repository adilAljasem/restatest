from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestKitchenFlowLog(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["pos.config"].create({"name": "Kitchen Flow Test POS"})
        cls.session = cls.env["pos.session"].create(
            {
                "config_id": cls.config.id,
                "user_id": cls.env.user.id,
            }
        )

    def _payload(self, **overrides):
        payload = {
            "name": "Order 701 / trace-701",
            "transaction_uuid": "trace-701",
            "config_id": self.config.id,
            "session_id": self.session.id,
            "order_uuid": "order-701",
            "order_reference": "Order 701",
            "device_identifier": "3",
            "priority": "parallel",
            "status": "warning",
            "started_at": "2026-09-01T10:00:00.100Z",
            "completed_at": "2026-09-01T10:00:02.445Z",
            "total_duration_ms": 2345.123,
            "client_summary": {"step_count": 2, "error_count": 1},
            "steps": [
                {
                    "sequence": 1,
                    "code": "display_sync",
                    "name": "Synchronize order with preparation display",
                    "category": "kitchen_display",
                    "status": "success",
                    "started_at": "2026-09-01T10:00:00.100Z",
                    "completed_at": "2026-09-01T10:00:00.600Z",
                    "duration_ms": 500.125,
                    "details": {"phase": "standard"},
                },
                {
                    "sequence": 2,
                    "code": "print_kitchen_receipt",
                    "name": "Print kitchen receipt: Epson",
                    "category": "printer",
                    "status": "error",
                    "started_at": "2026-09-01T10:00:00.601Z",
                    "completed_at": "2026-09-01T10:00:02.445Z",
                    "duration_ms": 1844.998,
                    "details": {"printer_name": "Epson", "printer_address": "192.168.1.12"},
                    "error_message": "Printer timeout",
                },
            ],
        }
        payload.update(overrides)
        return payload

    def test_configuration_defaults_are_safe(self):
        self.assertTrue(self.config.kitchen_flow_debug)
        self.assertTrue(self.config.kitchen_flow_notifications)
        self.assertEqual(self.config.kitchen_flow_priority, "printer_first")

    def test_create_from_ui_stores_exact_operations(self):
        log_id = self.env["bs.pos.kitchen.flow.log"].create_from_ui(self._payload())
        log = self.env["bs.pos.kitchen.flow.log"].browse(log_id)

        self.assertEqual(log.status, "warning")
        self.assertEqual(log.priority, "parallel")
        self.assertAlmostEqual(log.total_duration_ms, 2345.123, places=3)
        self.assertEqual(len(log.step_ids), 2)
        self.assertAlmostEqual(log.step_ids[0].duration_ms, 500.125, places=3)
        self.assertEqual(log.step_ids[1].details_json["printer_address"], "192.168.1.12")
        self.assertEqual(log.step_ids[1].error_message, "Printer timeout")

        duplicate_id = self.env["bs.pos.kitchen.flow.log"].create_from_ui(self._payload())
        self.assertEqual(duplicate_id, log.id)

    def test_rejects_session_from_another_configuration(self):
        other_config = self.env["pos.config"].create({"name": "Other Kitchen POS"})
        with self.assertRaises(ValidationError):
            self.env["bs.pos.kitchen.flow.log"].create_from_ui(
                self._payload(config_id=other_config.id)
            )

    def test_non_pos_user_cannot_create_trace(self):
        user = self.env["res.users"].create(
            {
                "name": "Kitchen Diagnostic Outsider",
                "login": "kitchen_diagnostic_outsider",
                "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        with self.assertRaises(AccessError):
            self.env["bs.pos.kitchen.flow.log"].with_user(user).create_from_ui(self._payload())

    def test_pos_user_can_store_but_cannot_read_diagnostics(self):
        user = self.env["res.users"].create(
            {
                "name": "Kitchen Diagnostic POS User",
                "login": "kitchen_diagnostic_pos_user",
                "group_ids": [(6, 0, [self.env.ref("point_of_sale.group_pos_user").id])],
            }
        )
        log_model = self.env["bs.pos.kitchen.flow.log"].with_user(user)
        log_id = log_model.create_from_ui(
            self._payload(transaction_uuid="trace-pos-user")
        )

        self.assertTrue(log_id)
        with self.assertRaises(AccessError):
            log_model.browse(log_id).read(["name"])

    def test_diagnostics_menu_requires_admin_and_debug_mode(self):
        menu = self.env.ref("bs_pos_kitchen_flow.kitchen_flow_log_menu")
        menu_id = menu.id

        self.assertEqual(menu.group_ids, self.env.ref("base.group_system"))

        self.assertNotIn(menu_id, self.env["ir.ui.menu"]._visible_menu_ids(debug=False))
        self.assertIn(menu_id, self.env["ir.ui.menu"]._visible_menu_ids(debug=True))

        manager = self.env["res.users"].create(
            {
                "name": "Kitchen Diagnostic POS Manager",
                "login": "kitchen_diagnostic_pos_manager",
                "group_ids": [
                    (6, 0, [self.env.ref("point_of_sale.group_pos_manager").id])
                ],
            }
        )
        self.assertNotIn(
            menu_id,
            self.env["ir.ui.menu"].with_user(manager)._visible_menu_ids(debug=True),
        )
