from freezegun import freeze_time

from odoo import Command
from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestTryOrderCatalog(TestPoSCommon, HttpCase):
    """Exercise the public catalogue with real pricelist and branch stock data."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref(
            "bs_pos_delivery_core.group_delivery_integration_manager"
        )
        cls.connection = cls.env["bs.delivery.connection"].create({
            "name": "CON-45 catalogue",
            "provider_id": cls.env["bs.delivery.provider"].search([
                ("code", "=", "tryorder"),
            ]).id,
            "company_id": cls.basic_config.company_id.id,
            "state": "connected",
            "auth_scheme": "api_key_secret",
            "webhook_secret": "tryorder-catalogue-test",
            "pos_config_ids": [Command.set(cls.basic_config.ids)],
        })
        cls.store = cls.env["bs.delivery.store.binding"].create({
            "connection_id": cls.connection.id,
            "pos_config_id": cls.basic_config.id,
            "remote_store_id": str(cls.basic_config.id),
        })
        cls.meal = cls.env["product.product"].create({
            "name": "CON-45 Truffle Burger", "available_in_pos": True,
            "list_price": 30,
        })
        cls.cheese = cls.env["product.product"].create({
            "name": "CON-45 White Cheese", "available_in_pos": False,
            "list_price": 9,
        })
        cls.fixed_product = cls.env["product.product"].create({
            "name": "CON-45 Fixed Extra", "available_in_pos": False, "list_price": 9,
        })
        cls.group = cls.env["pos.modifier.group"].create({
            "name": "CON-45 Burger Adds", "selection_type": "multiple",
            "max_qty": 10, "company_id": cls.basic_config.company_id.id,
        })
        cls.priced_option = cls.env["pos.modifier.option"].create({
            "name": "Swiss cheese", "group_id": cls.group.id,
            "option_type": "product", "product_id": cls.cheese.id,
            "price_mode": "product_pricelist",
        })
        cls.fixed_option = cls.env["pos.modifier.option"].create({
            "name": "Fixed Extra", "group_id": cls.group.id,
            "option_type": "product", "product_id": cls.fixed_product.id,
            "price_mode": "fixed_extra", "extra_price": 4,
        })
        cls.instruction = cls.env["pos.modifier.option"].create({
            "name": "No onions", "group_id": cls.group.id,
            "option_type": "instruction", "operation": "remove",
        })
        cls.meal.product_tmpl_id.write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": cls.group.id})],
        })
        cls.group_line = cls.meal.product_tmpl_id.modifier_group_line_ids
        cls.pricelist = cls.env["product.pricelist"].create({
            "name": "CON-45 Delivery",
            "company_id": cls.basic_config.company_id.id,
            "currency_id": cls.basic_config.company_id.currency_id.id,
            "item_ids": [Command.create({
                "applied_on": "0_product_variant", "product_id": cls.cheese.id,
                "compute_price": "fixed", "fixed_price": 2,
            })],
        })
        cls.connection.menu_pricelist_id = cls.pricelist
        cls.source = cls.basic_config.picking_type_id.default_location_src_id

    def _catalog(self, config=None, expected_status=200):
        self.env.flush_all()
        response = self.url_open(
            "/order_connector/catalog",
            params={"db": self.env.cr.dbname, "branch_id": (config or self.basic_config).id},
            headers={"X-Api-Key": "tryorder-catalogue-test",
                     "X-Odoo-Database": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, expected_status, response.text)
        return response.json()

    def _options(self, config=None):
        item = next(row for row in self._catalog(config)["items"]
                    if row["_id"] == str(self.meal.product_tmpl_id.id))
        return {row["modifier"]["_id"]: row["modifier"] for row in item["add_on"]}

    def _mid(self, option, group_line=None):
        return f"posmod-{option.id}-{(group_line or self.group_line).id}"

    def _item_options(self, product):
        item = next(row for row in self._catalog()["items"]
                    if row["_id"] == str(product.product_tmpl_id.id))
        return [row["modifier"] for row in item["add_on"]]

    def test_shared_group_gets_per_product_ids_and_availability(self):
        other = self.env["product.product"].create({
            "name": "CON-45 Second Burger", "available_in_pos": True, "list_price": 28,
        })
        other.product_tmpl_id.write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": self.group.id})],
        })
        other_line = other.product_tmpl_id.modifier_group_line_ids
        self.env["pos.product.modifier.option.line"].create({
            "group_line_id": other_line.id,
            "product_tmpl_id": other.product_tmpl_id.id,
            "option_id": self.fixed_option.id,
            "name": "Big fixed extra",
            "product_id": self.fixed_product.id,
            "price_mode": "fixed_extra", "extra_price": 7,
        })
        self.env["pos.product.modifier.option.line"].create({
            "group_line_id": other_line.id,
            "product_tmpl_id": other.product_tmpl_id.id,
            "option_id": self.instruction.id,
            "name": self.instruction.name,
            "price_mode": "included",
            "available": False,
        })
        meal = {row["_id"]: row for row in self._item_options(self.meal)}
        second = {row["_id"]: row for row in self._item_options(other)}

        self.assertFalse(set(meal) & set(second))
        self.assertEqual(
            {row["category"] for row in second.values()},
            {f"posmodgroup-{self.group.id}-{other_line.id}"},
        )
        self.assertEqual(meal[self._mid(self.fixed_option)]["price"], 4)
        self.assertEqual(second[self._mid(self.fixed_option, other_line)]["price"], 7)
        # Closed on the second burger only.
        self.assertIn(self._mid(self.instruction), meal)
        self.assertNotIn(self._mid(self.instruction, other_line), second)

    def _priced(self, config=None):
        return self._options(config)[self._mid(self.priced_option)]

    def test_modifier_price_modes_use_connection_menu_pricelist(self):
        options = self._options()
        priced = options[self._mid(self.priced_option)]
        self.assertEqual(priced["price"], 2)
        self.assertTrue(priced["status"])
        self.assertEqual(options[self._mid(self.fixed_option)]["price"], 4)
        self.assertEqual(options[self._mid(self.instruction)]["price"], 0)
        self.assertTrue(options[self._mid(self.instruction)]["status"])

    def test_modifier_uses_parent_override_product_and_zero_fixed_price(self):
        replacement = self.env["product.product"].create({
            "name": "CON-45 Replacement", "available_in_pos": False, "list_price": 11,
        })
        self.pricelist.item_ids = [Command.create({
            "applied_on": "0_product_variant", "product_id": replacement.id,
            "compute_price": "fixed", "fixed_price": 3,
        })]
        self.group_line.save_option_from_editor(self.priced_option.id, {
            "product_id": replacement.id, "price_mode": "product_pricelist",
        })
        self.assertEqual(self._priced()["price"], 3)
        self.group_line.save_option_from_editor(self.priced_option.id, {
            "price_mode": "fixed_extra", "extra_price": 0,
        })
        self.assertEqual(self._priced()["price"], 0)
        self.assertEqual(self.priced_option.product_id, self.cheese)
        self.assertEqual(self.priced_option.price_mode, "product_pricelist")

    def test_modifier_visibility_and_empty_group_match_blend(self):
        self.group_line.save_option_from_editor(self.fixed_option.id, {"available": False})
        self.assertNotIn(self._mid(self.fixed_option), self._options())
        self.cheese.delivery_available = False
        self.assertNotIn(self._mid(self.priced_option), self._options())
        self.cheese.delivery_available = True
        self.instruction.active = False
        self.priced_option.active = False
        self.assertEqual(self._options(), {})

    def test_modifier_status_respects_store_pause(self):
        self.store.paused = True
        self.assertFalse(self._priced()["status"])
        self.store.paused = False
        self.assertTrue(self._priced()["status"])

    @freeze_time("2026-10-25 12:00:00")
    def test_modifier_status_respects_branch_work_time(self):
        self.basic_config.company_id.partner_id.tz = "UTC"
        self.basic_config.write({
            "delivery_work_time_enabled": True,
            "delivery_work_start_time": 8,
            "delivery_work_end_time": 11,
            "delivery_second_shift_enabled": False,
        })
        self.assertFalse(self._priced()["status"])
        self.basic_config.delivery_work_end_time = 17
        self.assertTrue(self._priced()["status"])

    def test_product_priced_modifier_requires_active_menu_pricelist(self):
        self.connection.menu_pricelist_id = False
        payload = self._catalog(expected_status=409)
        self.assertFalse(payload["success"])
        self.assertIn("pricelist", payload["error"].lower())
        self.connection.menu_pricelist_id = self.pricelist
        self.pricelist.active = False
        self._catalog(expected_status=409)

    def test_odoo_stock_never_closes_a_modifier(self):
        self.cheese.is_storable = True
        quant = self.env["stock.quant"]
        self.assertTrue(self._priced()["status"])
        quant._update_available_quantity(self.cheese, self.source, -5)
        self.assertTrue(self._priced()["status"])
        self.assertEqual(self._priced()["price"], 2)
        # The branch pause still closes it.
        self.store.pause_receiving(reason="CON-45 pause")
        self.assertFalse(self._priced()["status"])
