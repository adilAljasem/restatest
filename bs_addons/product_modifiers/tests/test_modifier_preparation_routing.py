import json
import uuid

from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestModifierPreparationRouting(TransactionCase):
    """A modifier goes to its dish's kitchen screen (resta_kds) and is shown under the dish."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.parent_category, cls.modifier_category = cls.env["pos.category"].create([
            {"name": "Parent Kitchen"}, {"name": "Modifier Own Kitchen"}])
        cls.parent_product, cls.modifier_product, cls.normal_product = cls.env["product.product"].create([
            {"name": "Parent Product", "available_in_pos": True,
             "pos_categ_ids": [Command.set(cls.parent_category.ids)]},
            {"name": "Modifier Product", "available_in_pos": True,
             "pos_categ_ids": [Command.set(cls.modifier_category.ids)]},
            {"name": "Normal Product", "available_in_pos": True,
             "pos_categ_ids": [Command.set(cls.modifier_category.ids)]},
        ])
        cls.config = cls.env["pos.config"].create({"name": "Routing Test POS", "module_pos_restaurant": True})
        cls.parent_display = cls.env["resta.kds.display"].create({
            "name": "Parent Display", "pos_config_ids": [Command.set(cls.config.ids)],
            "category_ids": [Command.set(cls.parent_category.ids)]})
        cls.modifier_display = cls.env["resta.kds.display"].create({
            "name": "Modifier Display", "pos_config_ids": [Command.set(cls.config.ids)],
            "category_ids": [Command.set(cls.modifier_category.ids)]})
        cls.env["pos.session"].create({"config_id": cls.config.id})  # no UI checks (ZATCA onboarding)
        cls.session = cls.config.current_session_id

    def _order(self, with_uuid_link=True):
        parent_uuid = str(uuid.uuid4())
        lines = [
            Command.create({"product_id": self.parent_product.id, "qty": 1, "price_unit": 10, "price_subtotal": 10,
                            "price_subtotal_incl": 10, "uuid": parent_uuid}),
        ]
        order = self.env["pos.order"].create({
            "session_id": self.session.id, "config_id": self.config.id, "lines": lines,
            "amount_tax": 0, "amount_total": 0, "amount_paid": 0, "amount_return": 0})
        parent = order.lines
        modifier = self.env["pos.order.line"].create({
            "order_id": order.id, "product_id": self.modifier_product.id, "qty": 1, "price_unit": 2,
            "price_subtotal": 2, "price_subtotal_incl": 2, "uuid": str(uuid.uuid4()),
            "parent_line_id": str(parent.id),
            "modifier_parent_uuid": parent.uuid if with_uuid_link else False})
        normal = self.env["pos.order.line"].create({
            "order_id": order.id, "product_id": self.normal_product.id, "qty": 1, "price_unit": 5,
            "price_subtotal": 5, "price_subtotal_incl": 5, "uuid": str(uuid.uuid4())})
        return order, parent, modifier, normal

    def _send(self, order):
        order.write({"last_order_preparation_change": json.dumps({
            "lines": {l.uuid + "-": {"uuid": l.uuid, "quantity": l.qty} for l in order.lines},
            "metadata": {"serverDate": "2026-01-01 00:00:00"}})})

    def _lines(self, order, display):
        return order.resta_kds_ticket_ids.filtered(lambda t: t.display_id == display).line_ids

    def test_modifier_goes_to_parent_display_only(self):
        order, parent, modifier, normal = self._order()
        self._send(order)
        parent_lines = self._lines(order, self.parent_display)
        self.assertEqual(set(parent_lines.mapped("product_id").ids), {self.parent_product.id, self.modifier_product.id})
        shown = parent_lines.filtered(lambda l: l.product_id == self.modifier_product)
        self.assertEqual(shown.combo_parent_uuid, parent.uuid, "the modifier is shown under its dish")
        self.assertEqual(self._lines(order, self.modifier_display).product_id, self.normal_product,
                         "the modifier's own category screen does not get it")

    def test_backend_parent_reference_fallback(self):
        order, parent, modifier, _normal = self._order(with_uuid_link=False)
        self.assertEqual(modifier._resta_modifier_parent(), parent)
        self._send(order)
        self.assertIn(self.modifier_product, self._lines(order, self.parent_display).product_id)
        fields_to_load = self.env["pos.order.line"]._load_pos_data_fields(self.config)
        self.assertIn("modifier_parent_uuid", fields_to_load)

    def test_frontend_assets_compile(self):
        bundle = self.env["ir.qweb"]._get_asset_bundle("point_of_sale._assets_pos")
        self.assertTrue(bundle.js())
        self.assertTrue(bundle.css())
