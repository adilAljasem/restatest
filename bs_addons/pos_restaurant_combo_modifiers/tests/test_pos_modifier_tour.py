from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingHttpCommon
from odoo.tests import tagged


@tagged("post_install", "-at_install", "is_tour")
class TestPosModifierTour(AccountTestInvoicingHttpCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref("point_of_sale.group_pos_manager")
        cls.env.company.account_default_pos_receivable_account_id = (
            cls.company_data["default_account_receivable"]
        )
        cls.pos_user = cls.env["res.users"].create({
            "name": "Modifier Tour Cashier",
            "login": "modifier_tour_cashier",
            "password": "modifier_tour_cashier",
            "group_ids": [
                Command.link(cls.env.ref("base.group_user").id),
                Command.link(cls.env.ref("point_of_sale.group_pos_user").id),
                Command.link(cls.env.ref("stock.group_stock_user").id),
            ],
        })
        cash_payment_method = cls.env["pos.payment.method"].create({
            "name": "Cash",
            "journal_id": cls.company_data["default_journal_cash"].id,
            "receivable_account_id": cls.company_data["default_account_receivable"].id,
        })
        cls.main_pos_config = cls.env["pos.config"].create({
            "name": "Modifier Restaurant UAT",
            "module_pos_restaurant": True,
            "enable_product_modifiers": True,
            "journal_id": cls.company_data["default_journal_sale"].id,
            "invoice_journal_id": cls.company_data["default_journal_sale"].id,
            "payment_method_ids": [Command.link(cash_payment_method.id)],
        })
        cls.main_pos_config.floor_ids.unlink()
        main_floor = cls.env["restaurant.floor"].create({
            "name": "Modifier UAT Floor",
            "pos_config_ids": [Command.link(cls.main_pos_config.id)],
        })
        cls.main_floor_table_5 = cls.env["restaurant.table"].create({
            "table_number": 5,
            "floor_id": main_floor.id,
            "seats": 4,
            "position_h": 100,
            "position_v": 100,
        })
        cls.tour_modifier_product = cls.env["product.product"].create({
            "name": "UAT Extra Cheese",
            "available_in_pos": True,
            "list_price": 5,
            "taxes_id": [Command.clear()],
            "company_id": cls.env.company.id,
            "property_account_income_id": cls.company_data["default_account_revenue"].id,
        })
        cls.tour_parent_product = cls.env["product.product"].create({
            "name": "UAT Burger",
            "available_in_pos": True,
            "list_price": 20,
            "taxes_id": [Command.clear()],
            "company_id": cls.env.company.id,
            "property_account_income_id": cls.company_data["default_account_revenue"].id,
        })
        required_group = cls.env["pos.modifier.group"].create({
            "name": "UAT Required Extras",
            "selection_type": "multiple",
            "min_qty": 1,
            "max_qty": 3,
            "included_qty": 1,
            "company_id": cls.env.company.id,
            "option_ids": [Command.create({
                "name": "Extra Cheese",
                "option_type": "product",
                "operation": "add",
                "product_id": cls.tour_modifier_product.id,
                "price_mode": "fixed_extra",
                "extra_price": 5,
                "default_qty": 1,
                "max_qty": 3,
            })],
        })
        instruction_group = cls.env["pos.modifier.group"].create({
            "name": "UAT Instructions",
            "selection_type": "multiple",
            "min_qty": 0,
            "max_qty": 2,
            "company_id": cls.env.company.id,
            "option_ids": [Command.create({
                "name": "No Onion",
                "kitchen_name": "No Onion",
                "option_type": "instruction",
                "operation": "remove",
                "price_mode": "included",
                "default_qty": 0,
                "max_qty": 1,
            })],
        })
        cls.tour_parent_product.product_tmpl_id.write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [
                Command.create({
                    "modifier_group_id": required_group.id,
                    "sequence": 10,
                    "auto_open": True,
                }),
                Command.create({
                    "modifier_group_id": instruction_group.id,
                    "sequence": 20,
                    "auto_open": False,
                }),
            ],
        })

    def test_variant_selection_opens_modifiers_for_dual_use_product(self):
        size = self.env["product.attribute"].create({
            "name": "UAT Meal Size",
            "create_variant": "always",
            "value_ids": [
                Command.create({"name": "Small"}),
                Command.create({"name": "Large"}),
            ],
        })
        meal = self.env["product.template"].create({
            "name": "UAT Variant Meal",
            "available_in_pos": True,
            "allow_modifiers": True,
            "list_price": 20,
            "taxes_id": [Command.clear()],
            "company_id": self.env.company.id,
            "property_account_income_id": self.company_data["default_account_revenue"].id,
            "attribute_line_ids": [Command.create({
                "attribute_id": size.id,
                "value_ids": [Command.set(size.value_ids.ids)],
            })],
        })
        shared_group = (
            self.tour_parent_product.product_tmpl_id.modifier_group_line_ids[0]
            .modifier_group_id
        )
        self.env["pos.modifier.option"].create({
            "name": "Variant Meal as Modifier",
            "group_id": shared_group.id,
            "option_type": "product",
            "product_id": meal.product_variant_ids[0].id,
        })
        own_group = self.env["pos.modifier.group"].create({
            "name": "UAT Variant Meal Extras",
            "selection_type": "multiple",
            "min_qty": 1,
            "max_qty": 1,
            "company_id": self.env.company.id,
            "option_ids": [Command.create({
                "name": "Extra Cheese",
                "option_type": "product",
                "operation": "add",
                "product_id": self.tour_modifier_product.id,
                "price_mode": "fixed_extra",
                "extra_price": 5,
                "max_qty": 1,
            })],
        })
        self.env["pos.product.modifier.group.line"].create({
            "product_tmpl_id": meal.id,
            "modifier_group_id": own_group.id,
            "company_id": self.env.company.id,
        })
        self.assertEqual(len(meal.product_variant_ids), 2)
        self.assertTrue(meal.is_pos_modifier_product)
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_tour(
            f"/pos/ui/{self.main_pos_config.id}",
            "pos_variant_then_modifiers",
            login="modifier_tour_cashier",
        )

    def test_modifier_order_from_product_to_payment(self):
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_tour(
            f"/pos/ui/{self.main_pos_config.id}",
            "pos_combo_modifier_full_order",
            login="modifier_tour_cashier",
        )

        order = self.env["pos.order"].search([
            ("lines.product_id", "=", self.tour_parent_product.id),
        ], order="id desc", limit=1)
        self.assertTrue(order)
        self.assertEqual(order.table_id, self.main_floor_table_5)
        parent = order.lines.filtered(
            lambda line: line.product_id == self.tour_parent_product
        )
        self.assertEqual(len(parent), 1)
        self.assertEqual(len(parent.modifier_child_line_ids), 2)
        self.assertEqual(
            sum(parent.modifier_child_line_ids.mapped("modifier_qty_per_parent")),
            1.5,
        )
        self.assertEqual(
            parent.modifier_instruction_data["instructions"][0]["name"],
            "No Onion",
        )
        self.assertFalse(order.lines.filtered(
            lambda line: line.is_modifier_line and not line.modifier_parent_line_id
        ))
        self.assertEqual(order.amount_total, 22.5)

    def test_edit_line_modifiers_button(self):
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_tour(
            f"/pos/ui/{self.main_pos_config.id}",
            "pos_edit_line_modifiers",
            login="modifier_tour_cashier",
        )
        order = self.env["pos.order"].search([
            ("lines.product_id", "=", self.tour_parent_product.id),
        ], order="id desc", limit=1)
        parent = order.lines.filtered(
            lambda line: line.product_id == self.tour_parent_product
        )
        self.assertEqual(len(parent), 1)
        self.assertEqual(
            sum(parent.modifier_child_line_ids.mapped("modifier_qty_per_parent")),
            2.5,
        )
        self.assertFalse(order.lines.filtered(
            lambda line: line.is_modifier_line and not line.modifier_parent_line_id
        ))
        self.assertEqual(order.amount_total, 27.5)

    def test_product_specific_modifier_in_pos_and_receipt(self):
        template = self.tour_parent_product.product_tmpl_id
        template.action_sync_modifier_options()
        replacement = self.env["product.product"].create({
            "name": "Private Burger Cheese Product",
            "available_in_pos": True,
            "hide_from_pos_screen": True,
            "taxes_id": [Command.clear()],
            "company_id": self.env.company.id,
            "property_account_income_id": self.company_data["default_account_revenue"].id,
        })
        settings = template.modifier_option_line_ids.filtered(
            lambda line: line.option_id.product_id == self.tour_modifier_product
        )
        settings.write({
            "name": "Burger Cheese",
            "description": "Only for this burger",
            "extra_price": 9,
            "default_qty": 0.5,
            "product_id": replacement.id,
        })
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_tour(
            f"/pos/ui/{self.main_pos_config.id}",
            "pos_combo_modifier_product_override",
            login="modifier_tour_cashier",
        )
        order = self.env["pos.order"].search([
            ("lines.product_id", "=", self.tour_parent_product.id),
        ], order="id desc", limit=1)
        self.assertTrue(order)
        parent = order.lines.filtered(lambda line: line.product_id == self.tour_parent_product)
        paid = parent.modifier_child_line_ids.filtered(lambda line: not line.modifier_is_included)
        self.assertEqual(len(paid), 1)
        self.assertEqual(paid.price_unit, 9)
        self.assertEqual(paid.modifier_option_name, "Burger Cheese")
        self.assertEqual(paid.product_id, replacement)
        self.assertEqual(order.amount_total, 29)

    def test_kitchen_print_nests_modifiers_under_parent(self):
        category = self.env["pos.category"].create({"name": "Modifier Kitchen UAT"})
        self.tour_parent_product.pos_categ_ids = [Command.set(category.ids)]
        printer = self.env["pos.printer"].create({
            "name": "Modifier Test Printer",
            "printer_type": "epson_epos",
            "epson_printer_ip": "0.0.0.0",
            "product_categories_ids": [Command.set(category.ids)],
        })
        self.main_pos_config.write({
            "is_order_printer": True,
            "printer_ids": [Command.set(printer.ids)],
        })
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_tour(
            f"/pos/ui/{self.main_pos_config.id}",
            "pos_combo_modifier_kitchen_print",
            login="modifier_tour_cashier",
        )

    def test_kitchen_display_nests_modifiers_under_parent(self):
        category = self.env["pos.category"].create({"name": "Modifier Display UAT"})
        self.tour_parent_product.pos_categ_ids = [Command.set(category.ids)]
        self.env["resta.kds.display"].create({
            "name": "Modifier Display UAT",
            "pos_config_ids": [Command.link(self.main_pos_config.id)],
            "category_ids": [Command.link(category.id)],
        })
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_tour(
            f"/pos/ui/{self.main_pos_config.id}",
            "pos_combo_modifier_send_to_kitchen",
            login="modifier_tour_cashier",
        )
        kitchen_lines = self.env["resta.kds.ticket.line"].search([
            ("product_id", "=", self.tour_parent_product.id),
        ])
        self.assertTrue(kitchen_lines)
        self.assertTrue(any(
            {entry["name"] for entry in line.modifiers or []} >= {"Extra Cheese", "No Onion"}
            for line in kitchen_lines
        ))
