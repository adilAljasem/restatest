from pathlib import Path
from types import SimpleNamespace

from odoo.tests.common import TransactionCase


class TestKitchenFoundation(TransactionCase):
    def test_preparation_display_uses_standard_model(self):
        self.assertIn("pos.prep.display", self.env)

    def test_modifier_snapshot_is_stored_on_standard_preparation_line(self):
        fields_by_name = self.env["pos.prep.line"]._fields
        self.assertIn("modifier_snapshot_initialized", fields_by_name)
        self.assertEqual(fields_by_name["modifier_snapshot_data"].type, "json")
        self.assertEqual(fields_by_name["modifier_acknowledged_data"].type, "json")
        self.assertEqual(fields_by_name["modifier_display_data"].type, "json")

        loaded_fields = self.env["pos.prep.line"]._load_pos_preparation_data_fields()
        data_fields = self.env["pos.session"]._load_pos_data_relations(
            "pos.prep.line", loaded_fields
        )
        for field_name in (
            "prep_order_id",
            "product_id",
            "modifier_snapshot_initialized",
            "modifier_snapshot_data",
            "modifier_display_data",
        ):
            self.assertIn(field_name, data_fields)

    def test_kitchen_header_uses_standard_order(self):
        loaded_fields = self.env["pos.order"]._load_pos_preparation_data_fields()
        for field_name in (
            "name",
            "date_order",
            "kitchen_cashier_name",
            "kitchen_table_name",
            "kitchen_delivery_app",
            "kitchen_delivery_ref",
        ):
            self.assertIn(field_name, loaded_fields)

    def test_no_parallel_kitchen_model_is_created(self):
        module_path = Path(__file__).parents[1]
        model_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (module_path / "models").glob("*.py")
        )
        self.assertNotIn('_name = "pos.prep', model_sources)

    def test_modifier_delta_reports_added_removed_and_updated_once(self):
        previous_snapshot = [
            {
                "key": "product:1",
                "option_id": 1,
                "name": "Extra Cheese",
                "kind": "product",
                "operation": "add",
                "quantity": 1.0,
            },
            {
                "key": "instruction:2",
                "option_id": 2,
                "name": "No Onion",
                "kind": "instruction",
                "operation": "remove",
                "quantity": 1.0,
            },
        ]
        current_snapshot = [
            {
                "key": "product:1",
                "option_id": 1,
                "name": "Extra Cheese",
                "kind": "product",
                "operation": "add",
                "quantity": 2.0,
            },
            {
                "key": "instruction:3",
                "option_id": 3,
                "name": "Well Done",
                "kind": "instruction",
                "operation": "note",
                "quantity": 1.0,
            },
        ]

        delta = self.env["pos.order"]._modifier_display_delta(
            SimpleNamespace(modifier_acknowledged_data=previous_snapshot),
            current_snapshot,
        )

        self.assertEqual(len(delta), 3)
        self.assertEqual(
            {entry["key"]: entry["change_status"] for entry in delta},
            {
                "product:1": "updated",
                "instruction:3": "added",
                "instruction:2": "removed",
            },
        )

    def test_kitchen_snapshot_matches_product_modifiers_and_instructions(self):
        modifier_product = self.env["product.product"].create({
            "name": "Kitchen Extra Cheese",
            "available_in_pos": True,
        })
        group = self.env["pos.modifier.group"].create({
            "name": "Kitchen UAT",
            "selection_type": "multiple",
            "max_qty": 3,
        })
        product_option = self.env["pos.modifier.option"].create({
            "name": "Extra Cheese",
            "kitchen_name": "Cheese",
            "group_id": group.id,
            "option_type": "product",
            "operation": "add",
            "product_id": modifier_product.id,
        })
        instruction_option = self.env["pos.modifier.option"].create({
            "name": "No Onion",
            "group_id": group.id,
            "option_type": "instruction",
            "operation": "remove",
        })
        parent_line = self.env["pos.order.line"].new({
            "modifier_selection_data": {
                str(product_option.id): 2,
                str(instruction_option.id): 1,
            },
            "modifier_instruction_data": {
                "version": 1,
                "instructions": [{
                    "option_id": instruction_option.id,
                    "name": "No Onion",
                    "kitchen_name": "No Onion",
                    "operation": "remove",
                    "quantity": 1,
                }],
            },
        })

        snapshot = parent_line._modifier_kitchen_snapshot()

        self.assertEqual(len(snapshot), 2)
        self.assertEqual(
            {(entry["name"], entry["kind"], entry["operation"], entry["quantity"])
             for entry in snapshot},
            {
                ("Cheese", "product", "add", 2.0),
                ("No Onion", "instruction", "remove", 1.0),
            },
        )
