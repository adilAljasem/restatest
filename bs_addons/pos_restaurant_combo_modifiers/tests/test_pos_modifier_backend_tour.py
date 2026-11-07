from odoo import Command
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install", "is_tour")
class TestPosModifierBackendTour(HttpCase):
    def test_inline_product_modifier_option_editor(self):
        self.env.ref("base.user_admin").group_ids |= self.env.ref(
            "point_of_sale.group_pos_manager",
        )
        cheese = self.env["product.product"].create({"name": "Tour Cheese"})
        pickle = self.env["product.product"].create({"name": "Tour Pickle"})
        group = self.env["pos.modifier.group"].create({
            "name": "Tour Extras",
            "selection_type": "multiple",
            "option_ids": [Command.create({
                "name": "Tour Cheese",
                "option_type": "product",
                "product_id": cheese.id,
                "price_mode": "fixed_extra",
                "extra_price": 2.0,
            })],
        })
        product = self.env["product.template"].create({
            "name": "Tour Main Product",
            "available_in_pos": True,
            "allow_modifiers": True,
            "modifier_group_line_ids": [
                Command.create({"modifier_group_id": group.id}),
            ],
        })
        self.start_tour(
            f"/odoo/action-point_of_sale.product_template_action_pos_product/{product.id}",
            "pos_modifier_backend_single_table", login="admin",
        )
        self.env.invalidate_all()
        line = product.modifier_group_line_ids.option_line_ids
        self.assertEqual(len(line), 1)
        self.assertEqual(line.extra_price, 4.75)
        self.assertEqual(line.default_qty, 0.375)
        self.assertEqual(line.product_id, pickle)
        self.assertEqual(group.option_ids.product_id, cheese)
        self.assertEqual(group.option_ids.extra_price, 2.0)

    def test_new_group_options_can_be_edited_before_product_save(self):
        self.env.ref("base.user_admin").group_ids |= self.env.ref(
            "point_of_sale.group_pos_manager",
        )
        cheese = self.env["product.product"].create({"name": "New Group Cheese"})
        group = self.env["pos.modifier.group"].create({
            "name": "New Group Extras", "selection_type": "multiple",
            "option_ids": [Command.create({
                "name": "New Group Cheese", "option_type": "product",
                "product_id": cheese.id,
            })],
        })
        product = self.env["product.template"].create({
            "name": "New Group Main Product", "available_in_pos": True,
            "allow_modifiers": True,
        })
        self.start_tour(
            f"/odoo/action-point_of_sale.product_template_action_pos_product/{product.id}",
            "pos_modifier_backend_new_group", login="admin",
        )
        self.env.invalidate_all()
        self.assertEqual(product.modifier_group_line_ids.modifier_group_id, group)
        self.assertEqual(product.modifier_group_line_ids.option_line_ids.default_qty, 0.375)

    def test_fractional_product_option_updates_maximum(self):
        self.env.ref("base.user_admin").group_ids |= self.env.ref(
            "point_of_sale.group_pos_manager",
        )
        cheese = self.env["product.product"].create({"name": "Fractional Cheese"})
        group = self.env["pos.modifier.group"].create({
            "name": "Fractional Extras", "selection_type": "multiple", "max_qty": 100,
            "option_ids": [Command.create({
                "name": "Fractional Cheese", "option_type": "product",
                "product_id": cheese.id, "max_qty": 1,
            })],
        })
        product = self.env["product.template"].create({
            "name": "Fractional Main Product", "available_in_pos": True,
            "allow_modifiers": True,
            "modifier_group_line_ids": [
                Command.create({"modifier_group_id": group.id}),
            ],
        })
        self.start_tour(
            f"/odoo/action-point_of_sale.product_template_action_pos_product/{product.id}",
            "pos_modifier_backend_fractional_default", login="admin",
        )
        self.env.invalidate_all()
        override = product.modifier_group_line_ids.option_line_ids
        self.assertEqual(override.default_qty, 1.5)
        self.assertEqual(override.max_qty, 2)
        self.assertEqual(group.option_ids.max_qty, 1)

    def test_fractional_group_option_updates_maximum(self):
        self.env.ref("base.user_admin").group_ids |= self.env.ref(
            "point_of_sale.group_pos_manager",
        )
        cheese = self.env["product.product"].create({"name": "Group Fractional Cheese"})
        group = self.env["pos.modifier.group"].create({
            "name": "Group Fractional Extras", "selection_type": "multiple",
            "max_qty": 100,
            "option_ids": [Command.create({
                "name": "Group Fractional Cheese", "option_type": "product",
                "product_id": cheese.id, "max_qty": 1,
            })],
        })
        self.start_tour(
            f"/odoo/action-pos_restaurant_combo_modifiers.pos_modifier_group_action/{group.id}",
            "pos_modifier_group_fractional_default", login="admin",
        )
        self.env.invalidate_all()
        self.assertEqual(group.option_ids.default_qty, 1.5)
        self.assertEqual(group.option_ids.max_qty, 2)
