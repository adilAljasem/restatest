import json
import uuid

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestKitchenFlowPriority(TransactionCase):
    """«Kitchen screen first» and «in parallel» on the Resta kitchen screen (resta_kds)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.category = env["pos.category"].create({"name": "Kitchen"})
        cls.burger = env["product.product"].create({
            "name": "Burger", "available_in_pos": True, "list_price": 20,
            "pos_categ_ids": [(6, 0, cls.category.ids)],
        })
        cls.config = env["pos.config"].create({"name": "Flow POS", "module_pos_restaurant": True})
        cls.display = env["resta.kds.display"].create({
            "name": "Kitchen", "pos_config_ids": [(6, 0, cls.config.ids)],
        })
        cls.env["pos.session"].create({"config_id": cls.config.id})  # no UI checks (ZATCA onboarding)

    def _order(self, qty=1):
        self.line_uuid = str(uuid.uuid4())
        return self.env["pos.order"].create({
            "session_id": self.config.current_session_id.id,
            "config_id": self.config.id,
            "amount_tax": 0, "amount_total": 0, "amount_paid": 0, "amount_return": 0,
            "lines": [(0, 0, {
                "product_id": self.burger.id, "qty": qty, "price_unit": 20,
                "price_subtotal": 20 * qty, "price_subtotal_incl": 20 * qty,
                "uuid": self.line_uuid, "full_product_name": "Burger",
            })],
        })

    def _snapshot(self, order, qty):
        """What the POS stores after printing: the final snapshot sync."""
        order.write({"last_order_preparation_change": json.dumps({
            "lines": {self.line_uuid + "-": {"uuid": self.line_uuid, "quantity": qty}},
            "metadata": {"serverDate": "2026-01-01 00:00:00"},
        })})

    def test_default_priority(self):
        self.assertEqual(self.config.kitchen_flow_priority, "printer_first")

    def test_screen_first_sync_reaches_the_kitchen_before_the_snapshot(self):
        order = self._order(2)
        self.assertFalse(order.resta_kds_ticket_ids, "a draft order waits for «Send»")
        line = order.lines
        # the pre-send sync carries the flag: the screen gets the dishes on the POS right now
        order.with_context(resta_kds_fire=True).write({"lines": [(1, line.id, {"qty": 2})]})
        self.assertEqual(order.resta_kds_ticket_ids.line_ids.qty, 2)
        # the printers run, then the snapshot sync arrives: nothing is sent twice
        self._snapshot(order, 2)
        self.assertEqual(len(order.resta_kds_ticket_ids), 1)
        self.assertEqual(order.resta_kds_ticket_ids.line_ids.qty, 2)

    def test_parallel_call_is_idempotent(self):
        order = self._order(1)
        order.resta_kds_fire()
        order.resta_kds_fire()
        self._snapshot(order, 1)
        self.assertEqual(len(order.resta_kds_ticket_ids), 1)

    def test_fire_ignores_paid_orders(self):
        self.config.resta_kds_send_on_payment = False
        order = self._order(1)
        order.write({"state": "paid"})
        order.resta_kds_fire()
        self.assertFalse(order.resta_kds_ticket_ids)
