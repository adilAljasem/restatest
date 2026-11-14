import json
import uuid

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.resta_kds.models.pos_order import _modifier_delta


@tagged("post_install", "-at_install")
class TestKitchenFoundation(TransactionCase):
    def test_modifier_delta_reports_added_removed_and_updated_once(self):
        previous = [
            {"key": "product:1", "name": "Extra Cheese", "kind": "add", "qty": 1.0},
            {"key": "instruction:2", "name": "No Onion", "kind": "remove", "qty": 1.0},
        ]
        current = [
            {"key": "product:1", "name": "Extra Cheese", "kind": "add", "qty": 2.0},
            {"key": "instruction:3", "name": "Well Done", "kind": "instruction", "qty": 1.0},
        ]
        delta = _modifier_delta(previous, current)
        self.assertEqual(len(delta), 3)
        self.assertEqual({entry["key"]: entry["status"] for entry in delta},
                         {"product:1": "updated", "instruction:3": "added", "instruction:2": "removed"})

    def test_kitchen_screen_nests_modifiers_under_parent(self):
        category = self.env["pos.category"].create({"name": "Grill"})
        parent_product = self.env["product.product"].create({
            "name": "Burger", "available_in_pos": True, "pos_categ_ids": [(6, 0, category.ids)]})
        cheese = self.env["product.product"].create({"name": "Cheese", "available_in_pos": True})
        group = self.env["pos.modifier.group"].create({"name": "Burger extras", "selection_type": "multiple",
                                                       "max_qty": 3})
        option = self.env["pos.modifier.option"].create({
            "name": "Extra Cheese", "kitchen_name": "Cheese", "group_id": group.id, "option_type": "product",
            "operation": "add", "product_id": cheese.id})
        config = self.env["pos.config"].create({"name": "Modifiers KDS", "module_pos_restaurant": True})
        display = self.env["resta.kds.display"].create({"name": "Grill", "pos_config_ids": [(6, 0, config.ids)],
                                                        "category_ids": [(6, 0, category.ids)]})
        self.env["pos.session"].create({"config_id": config.id})  # no UI checks (ZATCA onboarding)
        order = self.env["pos.order"].create({
            "session_id": config.current_session_id.id, "config_id": config.id,
            "amount_tax": 0, "amount_total": 0, "amount_paid": 0, "amount_return": 0,
            "lines": [(0, 0, {"product_id": parent_product.id, "qty": 1, "price_unit": 20, "price_subtotal": 20,
                              "price_subtotal_incl": 20, "uuid": str(uuid.uuid4()),
                              "modifier_selection_data": {str(option.id): 1}})]})
        parent = order.lines

        def send():
            order.write({"last_order_preparation_change": json.dumps({
                "lines": {l.uuid + "-": {"uuid": l.uuid, "quantity": l.qty} for l in order.lines},
                "metadata": {"serverDate": "2026-01-01 00:00:00"}})})

        send()
        line = order.resta_kds_ticket_ids.line_ids
        self.assertEqual(line.product_id, parent_product)
        self.assertEqual([m["name"] for m in line.modifiers], ["Cheese"])
        # the cashier adds a second cheese after sending: the kitchen sees it as changed
        order.write({"lines": [(1, parent.id, {"modifier_selection_data": {str(option.id): 2}})]})
        line = order.resta_kds_ticket_ids.line_ids
        self.assertTrue(line.changed)
        self.assertEqual(line.modifiers[0]["status"], "updated")
        self.assertEqual(len(order.resta_kds_ticket_ids), 1)

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
