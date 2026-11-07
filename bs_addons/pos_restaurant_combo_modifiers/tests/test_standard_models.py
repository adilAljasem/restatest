from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestStandardModels(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.combo_product_a = cls.env["product.product"].create(
            {"name": "Combo Rule Product A", "available_in_pos": True}
        )
        cls.combo_product_b = cls.env["product.product"].create(
            {"name": "Combo Rule Product B", "available_in_pos": True}
        )

    def _create_combo(self, **values):
        values.setdefault("name", "Combo Rule Test")
        values.setdefault("qty_min", 1)
        values.setdefault("qty_max", 3)
        values.setdefault("qty_free", 1)
        values.setdefault(
            "combo_item_ids",
            [
                Command.create(
                    {
                        "product_id": self.combo_product_a.id,
                        "default_qty": 1,
                        "qty_max": 2,
                    }
                ),
                Command.create(
                    {
                        "product_id": self.combo_product_b.id,
                        "default_qty": 0,
                        "qty_max": 0,
                    }
                ),
            ],
        )
        return self.env["product.combo"].create(values)

    def test_native_models_are_available(self):
        model_names = (
            "product.combo",
            "product.combo.item",
            "pos.order",
            "pos.order.line",
            "product.template",
            "pos.config",
        )

        for model_name in model_names:
            with self.subTest(model=model_name):
                self.assertIn(model_name, self.env)

    def test_combo_keeps_standard_fields_and_adds_rules(self):
        combo = self._create_combo(display_type="list")

        self.assertEqual(combo.qty_max, 3)
        self.assertEqual(combo.qty_free, 1)
        self.assertIn("extra_price", combo.combo_item_ids._fields)
        self.assertEqual(combo.qty_min, 1)
        self.assertEqual(sum(combo.combo_item_ids.mapped("default_qty")), 1)
        self.assertEqual(combo.display_type, "list")

    def test_minimum_cannot_exceed_total_default_quantity(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._create_combo(qty_min=2)

    def test_total_default_quantity_cannot_exceed_combo_maximum(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._create_combo(
                qty_max=1,
                combo_item_ids=[
                    Command.create(
                        {
                            "product_id": self.combo_product_a.id,
                            "default_qty": 2,
                            "qty_max": 2,
                        }
                    )
                ],
            )

    def test_default_quantity_cannot_exceed_item_maximum(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._create_combo(
                combo_item_ids=[
                    Command.create(
                        {
                            "product_id": self.combo_product_a.id,
                            "default_qty": 2,
                            "qty_max": 1,
                        }
                    )
                ]
            )

    def test_zero_item_maximum_is_optional(self):
        combo = self._create_combo(
            combo_item_ids=[
                Command.create(
                    {
                        "product_id": self.combo_product_a.id,
                        "default_qty": 1,
                        "qty_max": 0,
                    }
                )
            ]
        )

        self.assertEqual(combo.combo_item_ids.qty_max, 0)

    def test_item_update_cannot_break_combo_minimum(self):
        combo = self._create_combo()

        with self.assertRaises(ValidationError), self.cr.savepoint():
            combo.combo_item_ids.filtered("default_qty").write({"default_qty": 0})

    def test_combo_rules_are_loaded_in_pos(self):
        combo_fields = self.env["product.combo"]._load_pos_data_fields(False)
        item_fields = self.env["product.combo.item"]._load_pos_data_fields(False)

        self.assertIn("qty_min", combo_fields)
        self.assertIn("display_type", combo_fields)
        self.assertIn("default_qty", item_fields)
        self.assertIn("qty_max", item_fields)

    def test_order_line_modifier_integrity_fields(self):
        order_line_fields = self.env["pos.order.line"]._load_pos_data_fields(False)

        expected_fields = {
            "modifier_parent_line_id",
            "modifier_child_line_ids",
            "modifier_option_id",
            "modifier_option_name",
            "is_modifier_line",
            "modifier_qty_per_parent",
            "modifier_is_included",
            "modifier_configuration_key",
            "modifier_instruction_data",
            "modifier_selection_data",
            "combo_item_extra_price",
        }
        self.assertTrue(expected_fields.issubset(order_line_fields))

        fields_by_name = self.env["pos.order.line"]._fields
        self.assertEqual(fields_by_name["modifier_parent_line_id"].type, "many2one")
        self.assertEqual(
            fields_by_name["modifier_parent_line_id"].comodel_name,
            "pos.order.line",
        )
        self.assertEqual(fields_by_name["modifier_parent_line_id"].ondelete, "cascade")
        self.assertEqual(fields_by_name["modifier_child_line_ids"].type, "one2many")
        self.assertEqual(
            fields_by_name["modifier_child_line_ids"].inverse_name,
            "modifier_parent_line_id",
        )
        self.assertEqual(fields_by_name["modifier_option_id"].type, "many2one")
        self.assertEqual(fields_by_name["modifier_option_name"].type, "char")
        self.assertFalse(fields_by_name["modifier_option_name"].related)
        self.assertEqual(fields_by_name["modifier_is_included"].type, "boolean")
        self.assertEqual(fields_by_name["modifier_instruction_data"].type, "json")
        self.assertEqual(fields_by_name["combo_item_extra_price"].type, "monetary")

    def test_backend_combo_modifier_details_view_exists(self):
        view = self.env.ref(
            "pos_restaurant_combo_modifiers.view_pos_order_form_combo_modifier_details"
        )

        self.assertEqual(view.model, "pos.order")
        self.assertIn("Combo &amp; Modifier Details", view.arch_db)

    def test_instruction_summary_uses_name_saved_at_sale(self):
        line = self.env["pos.order.line"].new({
            "modifier_instruction_data": {
                "version": 1,
                "instructions": [
                    {
                        "option_id": 100,
                        "name": "Well Done",
                        "group_name": "Cooking",
                        "quantity": 1,
                    }
                ],
            },
        })

        line._compute_combo_modifier_backend_details()

        self.assertEqual(line.modifier_instruction_summary, "Cooking: Well Done")
