from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestProductModifiers(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cheese = cls.env["product.template"].create({
            "name": "Test Modifier Cheese",
            "type": "consu",
            "available_in_pos": True,
            "list_price": 2.5,
        })
        cls.olives = cls.env["product.template"].create({
            "name": "Test Modifier Olives",
            "type": "consu",
            "available_in_pos": True,
            "list_price": 3.0,
        })
        cls.side = cls.env["product.template"].create({
            "name": "Test Modifier Side",
            "type": "consu",
            "available_in_pos": True,
        })
        cls.group = cls.env["modifiers.groups"].create({
            "name": "Test Modifier Group",
            "modifier_ids": [(6, 0, [cls.cheese.id, cls.olives.id])],
        })
        cls.parent = cls.env["product.template"].create({
            "name": "Test Modifier Pizza",
            "type": "consu",
            "available_in_pos": True,
            "is_modifier": True,
            "allow_portion": True,
            "modifier_grp_ids": [(6, 0, cls.group.ids)],
            "slice_product_ids": [(6, 0, cls.side.ids)],
        })
        cls.parent.get_modifier()
        cls.modifier_line = cls.parent.modifier_ids.filtered(
            lambda line: line.name == cls.cheese.product_variant_id
        )
        cls.modifier_line.write({
            "apply_selector": True,
            "min_selector": 1,
            "max_selector": 2,
        })

    def test_group_generation_and_selector_constraints(self):
        self.assertTrue(self.cheese.use_as_modifier)
        self.assertTrue(self.olives.use_as_modifier)
        self.assertEqual(
            set(self.parent.modifier_ids.mapped("name").ids),
            {self.cheese.product_variant_id.id, self.olives.product_variant_id.id},
        )
        self.assertEqual(set(self.parent.modifier_ids.mapped("price")), {2.5, 3.0})

        # Regenerating modifiers must not destroy prices or selector settings
        # configured by the user.
        self.modifier_line.write({"price": 7.25})
        self.parent.get_modifier()
        self.modifier_line.invalidate_recordset()
        self.assertEqual(self.modifier_line.price, 7.25)
        self.assertTrue(self.modifier_line.apply_selector)
        self.assertEqual(self.modifier_line.min_selector, 1)
        self.assertEqual(self.modifier_line.max_selector, 2)

        with self.assertRaises(ValidationError):
            self.env["product.modifiers"].create({
                "name": self.cheese.product_variant_id.id,
                "product_id": self.parent.id,
                "apply_selector": True,
                "min_selector": -1,
                "max_selector": 2,
            })

        with self.assertRaises(ValidationError):
            self.env["product.modifiers"].create({
                "name": self.cheese.product_variant_id.id,
                "product_id": self.parent.id,
                "apply_selector": True,
                "min_selector": 3,
                "max_selector": 2,
            })

    def test_modifier_tabs_and_variant_button(self):
        self.parent.modifier_ids.unlink()
        self.parent.product_variant_id.get_modifier()
        self.assertTrue(self.parent.modifier_ids)

        variant_form = self.env["product.product"].get_view(
            view_id=self.env.ref("product.product_normal_form_view").id,
            view_type="form",
        )["arch"]
        self.assertIn('name="product_modifiers"', variant_form)
        self.assertIn('name="product_modifier_sides"', variant_form)

    def test_pos_payload_and_modifier_order_line_fields(self):
        config = self.env["pos.config"].create({"name": "Test Modifiers POS"})
        session = self.env["pos.session"].create({"config_id": config.id})
        category = self.env["pos.category"].create({
            "name": "Test Modifier Category",
            "is_modifier": True,
        })
        category_modifier = self.env["pos.modifiers"].create({
            "name": self.cheese.product_variant_id.id,
            "desc": "Test category modifier",
            "price": 2.5,
            "pos_categoryid": category.id,
        })
        attribute = self.env["product.attribute"].create({
            "name": "Test Modifier Attribute",
            "is_modifier": True,
        })
        models_to_load = session._load_pos_data_models(config)
        self.assertTrue({"product.modifiers", "pos.modifiers", "modifiers.groups"}.issubset(models_to_load))

        payload = session.load_data(models_to_load)
        parent_payload = next(
            product
            for product in payload["product.product"]
            if product["id"] == self.parent.product_variant_id.id
        )
        self.assertTrue(parent_payload["is_modifier"])
        self.assertTrue(parent_payload["allow_portion"])
        self.assertTrue(parent_payload["modifier_ids"])
        self.assertTrue(parent_payload["slice_product_ids"])
        category_payload = next(
            item for item in payload["pos.category"] if item["id"] == category.id
        )
        self.assertTrue(category_payload["is_modifier"])
        self.assertIn(category_modifier.id, category_payload["modifier_ids"])
        attribute_payload = next(
            item for item in payload["product.attribute"] if item["id"] == attribute.id
        )
        self.assertTrue(attribute_payload["is_modifier"])

        order = self.env["pos.order"].create({
            "session_id": session.id,
            "config_id": config.id,
            "amount_tax": 0,
            "amount_total": 0,
            "amount_paid": 0,
            "amount_return": 0,
        })
        parent_line = self.env["pos.order.line"].create({
            "order_id": order.id,
            "product_id": self.parent.product_variant_id.id,
            "qty": 1,
            "price_unit": 10,
            "price_subtotal": 10,
            "price_subtotal_incl": 10,
        })
        modifier_line = self.env["pos.order.line"].create({
            "order_id": order.id,
            "product_id": self.cheese.product_variant_id.id,
            "qty": 0.5,
            "price_unit": 2.5,
            "price_subtotal": 1.25,
            "price_subtotal_incl": 1.25,
            "parent_line_id": str(parent_line.id),
            "portion": "Left Half",
            "modifier_line_id": self.modifier_line.id,
            "modifier_selection_qty": 1,
        })
        fields_to_load = self.env["pos.order.line"]._load_pos_data_fields(config)
        loaded_line = modifier_line.read(fields_to_load, load=False)[0]
        self.assertTrue({
            "parent_line_id",
            "portion",
            "modifier_line_id",
            "modifier_selection_qty",
        }.issubset(fields_to_load))
        self.assertEqual(loaded_line["parent_line_id"], str(parent_line.id))
        self.assertEqual(loaded_line["portion"], "Left Half")
        self.assertEqual(loaded_line["modifier_line_id"], self.modifier_line.id)
        self.assertEqual(loaded_line["modifier_selection_qty"], 1)

    def test_modifier_dependencies_bypass_pos_category_filter(self):
        allowed_category = self.env["pos.category"].create({
            "name": "Allowed Modifier Parent Category",
        })
        excluded_category = self.env["pos.category"].create({
            "name": "Excluded Modifier Products Category",
        })
        self.parent.pos_categ_ids = allowed_category
        (self.cheese | self.olives | self.side).pos_categ_ids = excluded_category
        config = self.env["pos.config"].create({
            "name": "Restricted Modifiers POS",
            "limit_categories": True,
            "iface_available_categ_ids": [(6, 0, allowed_category.ids)],
        })
        session = self.env["pos.session"].create({"config_id": config.id})

        payload = session.load_data(session._load_pos_data_models(config))
        loaded_product_ids = {
            product["id"] for product in payload["product.product"]
        }
        self.assertTrue(
            {
                self.parent.product_variant_id.id,
                self.cheese.product_variant_id.id,
                self.olives.product_variant_id.id,
                self.side.product_variant_id.id,
            }.issubset(loaded_product_ids)
        )

    def test_pos_assets_compile(self):
        assets = self.env["ir.qweb"]._get_asset_bundle("point_of_sale._assets_pos")
        self.assertTrue(assets.js())
        self.assertTrue(assets.css())
