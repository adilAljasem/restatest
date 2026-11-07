from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestDeliveryOrderBinding(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Binding = cls.env["bs.delivery.order.binding"]
        cls.provider = cls.env["bs.delivery.provider"].create(
            {"name": "Binding Test Provider", "code": "binding_test"}
        )
        cls.connection = cls.env["bs.delivery.connection"].create(
            {
                "name": "Binding Test Connection",
                "provider_id": cls.provider.id,
            }
        )

    def test_get_or_create_is_idempotent(self):
        first, first_created = self.Binding.get_or_create(
            self.connection,
            "  EXT-ORDER-001  ",
            {"external_reference": "REF-001", "external_source": "Marketplace"},
        )
        second, second_created = self.Binding.get_or_create(
            self.connection,
            "EXT-ORDER-001",
            {"external_reference": "SHOULD-NOT-OVERWRITE"},
        )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first, second)
        self.assertEqual(first.external_order_id, "EXT-ORDER-001")
        self.assertEqual(first.external_reference, "REF-001")
        self.assertEqual(
            self.Binding.search_count(
                [
                    ("connection_id", "=", self.connection.id),
                    ("external_order_id", "=", "EXT-ORDER-001"),
                ]
            ),
            1,
        )

    def test_same_external_id_is_allowed_on_another_connection(self):
        second_connection = self.env["bs.delivery.connection"].create(
            {
                "name": "Second Binding Connection",
                "provider_id": self.provider.id,
            }
        )

        first, _created = self.Binding.get_or_create(self.connection, "SAME-ID")
        second, _created = self.Binding.get_or_create(second_connection, "SAME-ID")

        self.assertNotEqual(first, second)

    def test_synced_binding_requires_pos_order(self):
        binding, _created = self.Binding.get_or_create(self.connection, "EXT-SYNC-001")

        with self.assertRaises(ValidationError):
            binding.mark_sync_success(external_status="accepted")

    def test_binding_error_state_keeps_diagnostics(self):
        binding, _created = self.Binding.get_or_create(self.connection, "EXT-ERROR-001")

        binding.mark_sync_error("Provider validation failed")

        self.assertEqual(binding.sync_state, "error")
        self.assertEqual(binding.last_error, "Provider validation failed")
        self.assertTrue(binding.last_sync_date)

    def test_company_record_rule_isolates_bindings(self):
        visible, _created = self.Binding.get_or_create(self.connection, "VISIBLE-BINDING")
        other_company = self.env["res.company"].create({"name": "Binding Other Company"})
        other_connection = self.env["bs.delivery.connection"].with_company(other_company).create(
            {
                "name": "Other Company Binding Connection",
                "provider_id": self.provider.id,
                "company_id": other_company.id,
            }
        )
        hidden, _created = self.Binding.get_or_create(other_connection, "HIDDEN-BINDING")
        manager = new_test_user(
            self.env,
            login="binding_company_manager",
            groups="bs_pos_delivery_core.group_delivery_integration_manager",
            company_id=self.env.company.id,
        )

        bindings = self.Binding.with_user(manager).search(
            [("id", "in", [visible.id, hidden.id])]
        )

        self.assertEqual(bindings, visible)
