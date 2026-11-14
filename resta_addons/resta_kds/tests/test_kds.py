import json
import uuid

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "resta_kds")
class TestKds(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.cat_food = env["pos.category"].create({"name": "مأكولات"})
        cls.cat_drink = env["pos.category"].create({"name": "مشروبات"})
        cls.burger = env["product.product"].create({"name": "برجر", "available_in_pos": True, "list_price": 20,
                                                    "pos_categ_ids": [(6, 0, cls.cat_food.ids)]})
        cls.fries = env["product.product"].create({"name": "بطاطس", "available_in_pos": True, "list_price": 8,
                                                   "pos_categ_ids": [(6, 0, cls.cat_food.ids)]})
        cls.cola = env["product.product"].create({"name": "كولا", "available_in_pos": True, "list_price": 5,
                                                  "pos_categ_ids": [(6, 0, cls.cat_drink.ids)]})
        cls.config = env["pos.config"].create({"name": "كاشير الاختبار", "module_pos_restaurant": True})
        cls.kitchen = env["resta.kds.display"].create({
            "name": "المطبخ", "pos_config_ids": [(6, 0, cls.config.ids)], "category_ids": [(6, 0, cls.cat_food.ids)]})
        cls.bar = env["resta.kds.display"].create({
            "name": "المشروبات", "pos_config_ids": [(6, 0, cls.config.ids)], "category_ids": [(6, 0, cls.cat_drink.ids)]})
        cls.env["pos.session"].create({"config_id": cls.config.id})  # no UI checks (ZATCA onboarding)
        cls.session = cls.config.current_session_id
        cls.cook = new_test_user(env, login="kds_cook", groups="resta_kds.group_kds_user")
        cls.stranger = new_test_user(env, login="kds_stranger", groups="base.group_user")

    def make_order(self, items, **vals):
        lines, self.uuids = [], {}
        for product, qty in items:
            u = str(uuid.uuid4())
            self.uuids[product.id] = u
            lines.append((0, 0, {"product_id": product.id, "qty": qty, "price_unit": product.lst_price,
                                 "price_subtotal": qty * product.lst_price,
                                 "price_subtotal_incl": qty * product.lst_price, "uuid": u,
                                 "full_product_name": product.name}))
        vals.update({"session_id": self.session.id, "config_id": self.config.id, "lines": lines,
                     "amount_tax": 0, "amount_total": 0, "amount_paid": 0, "amount_return": 0})
        return self.env["pos.order"].create(vals)

    def send(self, order, quantities):
        """What the POS does on «Send»: store the state sent to the kitchen."""
        change = {"lines": {self.uuids[p.id] + "-": {"uuid": self.uuids[p.id], "quantity": q}
                            for p, q in quantities}, "metadata": {"serverDate": "2026-01-01 00:00:00"}}
        order.write({"last_order_preparation_change": json.dumps(change)})

    def tickets(self, order, display):
        return order.resta_kds_ticket_ids.filtered(lambda t: t.display_id == display)

    # ------------------------------------------------------------------ dispatch
    def test_draft_order_not_sent_until_send(self):
        order = self.make_order([(self.burger, 1)])
        self.assertFalse(order.resta_kds_ticket_ids)

    def test_send_routes_by_station(self):
        order = self.make_order([(self.burger, 2), (self.cola, 1)])
        self.send(order, [(self.burger, 2), (self.cola, 1)])
        k, b = self.tickets(order, self.kitchen), self.tickets(order, self.bar)
        self.assertEqual(len(k), 1)
        self.assertEqual(k.line_ids.mapped("name"), ["برجر"])
        self.assertEqual(b.line_ids.mapped("name"), ["كولا"])
        self.assertEqual(order.resta_kds_status, "preparing")

    def test_additions_and_cancellations(self):
        order = self.make_order([(self.burger, 2), (self.fries, 1)])
        self.send(order, [(self.burger, 2), (self.fries, 1)])
        order.lines.filtered(lambda l: l.product_id == self.burger).qty = 3
        self.send(order, [(self.burger, 3)])  # one more burger, fries removed
        k = self.tickets(order, self.kitchen).sorted("id")
        self.assertEqual(len(k), 2)
        self.assertEqual(k[1].send_index, 2)
        self.assertEqual(k[1].line_ids.qty, 1)
        fries = k[0].line_ids.filtered(lambda l: l.product_id == self.fries)
        self.assertEqual(fries.qty_cancelled, 1)

    def test_idempotent(self):
        order = self.make_order([(self.burger, 1)])
        self.send(order, [(self.burger, 1)])
        self.send(order, [(self.burger, 1)])
        order._resta_kds_dispatch()
        self.assertEqual(len(self.tickets(order, self.kitchen)), 1)

    def test_payment_sends_what_was_not_sent(self):
        order = self.make_order([(self.burger, 1), (self.cola, 2)])
        order.write({"state": "paid"})
        self.assertEqual(self.tickets(order, self.kitchen).line_ids.qty, 1)
        self.assertEqual(self.tickets(order, self.bar).line_ids.qty, 2)
        # closing the session later never adds or cancels anything
        order.write({"state": "done"})
        self.assertEqual(len(order.resta_kds_ticket_ids), 2)

    def test_payment_option_off(self):
        self.config.resta_kds_send_on_payment = False
        order = self.make_order([(self.burger, 1)])
        order.write({"state": "paid"})
        self.assertFalse(order.resta_kds_ticket_ids)

    def test_cancelled_order(self):
        order = self.make_order([(self.burger, 2)])
        self.send(order, [(self.burger, 2)])
        order.write({"state": "cancel"})
        self.assertTrue(self.tickets(order, self.kitchen).cancelled)

    def test_self_order_goes_straight_to_kitchen(self):
        if "source" not in self.env["pos.order"]._fields:
            self.skipTest("pos_self_order not installed")
        order = self.make_order([(self.burger, 1)], source="mobile")
        self.assertEqual(len(self.tickets(order, self.kitchen)), 1)

    # ------------------------------------------------------------------ kitchen actions
    def test_bump_makes_order_ready_and_calls_hook(self):
        order = self.make_order([(self.burger, 1), (self.cola, 1)])
        self.send(order, [(self.burger, 1), (self.cola, 1)])
        self.tickets(order, self.kitchen).with_user(self.cook).kds_next()
        self.assertEqual(order.resta_kds_status, "preparing")  # drinks not ready yet
        self.tickets(order, self.bar).with_user(self.cook).kds_next()
        self.assertEqual(order.resta_kds_status, "ready")
        self.assertTrue(order.resta_kds_ready_date)

    def test_line_toggle_auto_finishes(self):
        order = self.make_order([(self.burger, 1), (self.fries, 1)])
        self.send(order, [(self.burger, 1), (self.fries, 1)])
        ticket = self.tickets(order, self.kitchen)
        ticket.line_ids[0].with_user(self.cook).kds_toggle()
        self.assertFalse(ticket.done)
        ticket.line_ids[1].with_user(self.cook).kds_toggle()
        self.assertTrue(ticket.done)

    def test_multi_stage(self):
        self.env["resta.kds.stage"].create({"display_id": self.kitchen.id, "name": "جاهز", "sequence": 50,
                                            "is_ready": True})
        order = self.make_order([(self.burger, 1)])
        self.send(order, [(self.burger, 1)])
        ticket = self.tickets(order, self.kitchen)
        ticket.with_user(self.cook).kds_next()
        self.assertTrue(ticket.stage_id.is_ready)
        self.assertFalse(ticket.done)
        ticket.with_user(self.cook).kds_next()
        self.assertTrue(ticket.done)

    def test_recall(self):
        order = self.make_order([(self.burger, 1)])
        self.send(order, [(self.burger, 1)])
        ticket = self.tickets(order, self.kitchen)
        ticket.with_user(self.cook).kds_done()
        ticket.with_user(self.cook).kds_recall()
        self.assertFalse(ticket.done)

    def test_screen_needs_kitchen_group(self):
        order = self.make_order([(self.burger, 1)])
        self.send(order, [(self.burger, 1)])
        with self.assertRaises(UserError):
            self.tickets(order, self.kitchen).with_user(self.stranger).kds_next()

    def test_screen_payload(self):
        order = self.make_order([(self.burger, 2)])
        self.send(order, [(self.burger, 2)])
        data = self.kitchen.with_user(self.cook).kds_load()
        self.assertEqual(len(data["tickets"]), 1)
        self.assertEqual(data["tickets"][0]["lines"][0]["qty"], 2)
        self.assertTrue(data["display"]["channel"])

    def test_default_display_has_one_stage(self):
        self.assertEqual(len(self.kitchen.stage_ids), 1)
