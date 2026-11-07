"""Connection acceptance policy and rejection behavior on real POS records."""

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.common import TestPoSCommon


@tagged("post_install", "-at_install")
class TestDeliveryAcceptancePolicy(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref("bs_pos_delivery_core.group_delivery_integration_manager")
        cls.provider = cls.env["bs.delivery.provider"].create(
            {"name": "Acceptance Test Provider", "code": "acceptance_test"}
        )
        cls.connection = cls.env["bs.delivery.connection"].create(
            {"name": "Acceptance Test Connection", "provider_id": cls.provider.id}
        )
        cls.product = cls.env["product.product"].create({
            "name": "Acceptance Test Meal", "available_in_pos": True,
            "list_price": 10.0, "taxes_id": [Command.clear()],
            "property_account_income_id": cls.sales_account.id,
        })
        cls.basic_config.open_ui()
        cls.store = cls.env["bs.delivery.store.binding"].create({
            "connection_id": cls.connection.id, "pos_config_id": cls.basic_config.id,
            "remote_store_id": "ACCEPTANCE-STORE",
        })

    def _create_order(self, suffix="order", store=None):
        return self.env["bs.delivery.order"]._with_delivery_internal_update().create({
            "connection_id": self.connection.id, "store_binding_id": (store or self.store).id,
            "external_order_id": "%s-%s" % (self._testMethodName, suffix),
            "currency_id": self.company.currency_id.id,
            "items_subtotal": 10.0, "vat_amount": 0.0,
            "amount_total": 10.0, "is_prepaid": False,
            "line_ids": [Command.create({
                "name": self.product.name, "product_id": self.product.id,
                "quantity": 1.0, "price_unit": 10.0,
                "price_subtotal": 10.0, "vat_amount": 0.0,
            })],
        })

    def test_connection_policy_defaults_to_manual(self):
        self.assertEqual(self.connection.delivery_accept_action, "manual")
        order = self._create_order("branch-manual")
        self.assertEqual(order.state, "received")
        self.assertFalse(order.pos_order_id)
        self.assertFalse(order.transition_ids)

    def test_connection_policy_is_shared_by_its_branches(self):
        self.assertEqual(self.connection.delivery_accept_action, "manual")
        other_config = self.basic_config.copy({"name": "Acceptance second POS"})
        other_store = self.env["bs.delivery.store.binding"].create({
            "connection_id": self.connection.id,
            "pos_config_id": other_config.id,
            "remote_store_id": "ACCEPTANCE-SECOND-STORE",
        })
        reason = self.env["bs.delivery.reject.reason"].create({
            "name": "Connection rejection", "code": "connection_rejection_test",
        })
        self.connection.write({
            "delivery_auto_reject_reason_id": reason.id,
            "delivery_accept_action": "reject",
        })
        for store in (self.store, other_store):
            order = self._create_order(str(store.id), store=store)
            order._apply_initial_acceptance_policy()
            self.assertEqual(order.state, "rejected")
            self.assertEqual(order.reject_reason_id, reason)

    def test_auto_accept_requires_default_invoice_customer(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.connection.delivery_accept_action = "accept"

    def test_auto_reject_requires_reason(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.connection.delivery_accept_action = "reject"

    def test_connection_auto_reject_uses_configured_reason(self):
        reason = self.env["bs.delivery.reject.reason"].create({
            "name": "Outside delivery area", "code": "outside_area_test",
        })
        self.connection.write({
            "delivery_auto_reject_reason_id": reason.id,
            "delivery_accept_action": "reject",
        })
        order = self._create_order("branch-reject")
        self.assertEqual(order.state, "rejected")
        self.assertEqual(order.reject_reason_id, reason)
        self.assertFalse(order.pos_order_id)

    def test_pause_overrides_manual_policy(self):
        self.store.write({"paused": True, "pause_reason": "Paused for test"})
        order = self._create_order("paused")
        self.assertEqual(order.state, "rejected")
        self.assertEqual(order.rejection_note, "Paused for test")
        self.assertFalse(order.pos_order_id)

    def test_failed_local_acceptance_preserves_received_state(self):
        order = self._create_order()
        order._with_delivery_internal_update().write({"customer_name": "Test Customer"})
        self.basic_config.current_session_id.action_pos_session_closing_control()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            order.action_accept()
        self.assertEqual(order.state, "received")
        self.assertFalse(order.pos_order_id)
        self.assertFalse(order.binding_id.pos_order_id)
        self.assertFalse(order.transition_ids)

    def test_manual_accept_without_payload_customer_opens_partner_picker(self):
        order = self._create_order("choose-customer")
        action = order.action_accept()
        self.assertEqual(action["res_model"], "bs.delivery.accept.customer.wizard")
        self.assertEqual(action["context"]["default_order_id"], order.id)
        self.assertEqual(order.state, "received")
