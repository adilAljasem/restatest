from unittest.mock import patch

from odoo.tests import TransactionCase, tagged
from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestDeliveryProductTemplate(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env["product.template"].create(
            {
                "name": "Delivery Test Burger",
                "available_in_pos": True,
            }
        )

    def test_delivery_available_default(self):
        self.assertTrue(self.product.delivery_available)

    def test_adapter_operations_contract(self):
        adapter = self.env["bs.delivery.adapter"]
        self.assertIn("update_product_availability", adapter.OPERATIONS)
        self.assertIn("push_menu", adapter.OPERATIONS)
        self.assertIn("sync_products", adapter.OPERATIONS)

    def test_action_delivery_toggle_availability(self):
        action = self.product.action_delivery_toggle_availability()
        self.assertFalse(self.product.delivery_available)
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "success")

        action = self.product.action_delivery_toggle_availability()
        self.assertTrue(self.product.delivery_available)

    def test_action_delivery_sync_product_permission(self):
        # Admin can run sync
        action = self.product.action_delivery_sync_product()
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "success")

        # Non-delivery manager user raises AccessError
        user = new_test_user(
            self.env,
            login="test_cashier",
            groups="base.group_user,point_of_sale.group_pos_user",
        )
        with self.assertRaises(AccessError):
            self.product.with_user(user).action_delivery_sync_product()

