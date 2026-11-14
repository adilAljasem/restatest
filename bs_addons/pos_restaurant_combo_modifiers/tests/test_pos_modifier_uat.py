from odoo import Command
from odoo.addons.point_of_sale.tests.common import CommonPosTest
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tools import format_amount


@tagged("post_install", "-at_install")
class TestPosModifierUAT(CommonPosTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        product_model = cls.env["product.product"]
        cls.parent_product = product_model.create({
            "name": "UAT Burger",
            "available_in_pos": True,
            "list_price": 20,
            "taxes_id": [Command.clear()],
            "company_id": cls.env.company.id,
        })
        cls.plain_product = product_model.create({
            "name": "UAT Plain Product",
            "available_in_pos": True,
            "list_price": 10,
            "taxes_id": [Command.clear()],
            "company_id": cls.env.company.id,
        })
        cls.fixed_modifier_product = product_model.create({
            "name": "UAT Extra Cheese",
            "available_in_pos": True,
            "list_price": 8,
            "taxes_id": [Command.clear()],
            "company_id": cls.env.company.id,
        })
        cls.pricelist_modifier_product = product_model.create({
            "name": "UAT Premium Sauce",
            "available_in_pos": True,
            "list_price": 12,
            "taxes_id": [Command.clear()],
            "company_id": cls.env.company.id,
        })
        cls.required_group = cls.env["pos.modifier.group"].create({
            "name": "UAT Required Extras",
            "selection_type": "multiple",
            "min_qty": 1,
            "max_qty": 4,
            "included_qty": 1,
            "company_id": cls.env.company.id,
        })
        cls.fixed_option = cls.env["pos.modifier.option"].create({
            "name": "Extra Cheese",
            "group_id": cls.required_group.id,
            "option_type": "product",
            "operation": "add",
            "product_id": cls.fixed_modifier_product.id,
            "price_mode": "fixed_extra",
            "extra_price": 5,
            "default_qty": 1,
            "max_qty": 3,
        })
        cls.pricelist_option = cls.env["pos.modifier.option"].create({
            "name": "Premium Sauce",
            "group_id": cls.required_group.id,
            "option_type": "product",
            "operation": "add",
            "product_id": cls.pricelist_modifier_product.id,
            "price_mode": "product_pricelist",
            "default_qty": 0,
            "max_qty": 3,
        })
        cls.optional_group = cls.env["pos.modifier.group"].create({
            "name": "UAT Instructions",
            "selection_type": "multiple",
            "min_qty": 0,
            "max_qty": 2,
            "included_qty": 0,
            "company_id": cls.env.company.id,
        })
        cls.instruction_option = cls.env["pos.modifier.option"].create({
            "name": "No Onion",
            "kitchen_name": "No Onion",
            "group_id": cls.optional_group.id,
            "option_type": "instruction",
            "operation": "remove",
            "price_mode": "included",
            "default_qty": 0,
            "max_qty": 1,
        })
        cls.parent_product.product_tmpl_id.write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [
                Command.create({
                    "modifier_group_id": cls.required_group.id,
                    "sequence": 10,
                    "auto_open": True,
                }),
                Command.create({
                    "modifier_group_id": cls.optional_group.id,
                    "sequence": 20,
                    "auto_open": False,
                }),
            ],
        })
        cls.modifier_pricelist = cls.env["product.pricelist"].create({
            "name": "UAT Modifier Pricelist",
            "currency_id": cls.env.company.currency_id.id,
            "item_ids": [Command.create({
                "applied_on": "0_product_variant",
                "product_id": cls.pricelist_modifier_product.id,
                "compute_price": "fixed",
                "fixed_price": 7,
            })],
        })

    def _create_order(self, product=None, quantity=1, discount=0, pricelist=None):
        product = product or self.parent_product
        order, _refund = self.create_backend_pos_order({
            "pos_config": self.pos_config_usd,
            "order_data": {
                "pricelist_id": (pricelist or self.pos_config_usd.pricelist_id).id,
            },
            "line_data": [{
                "product_id": product.id,
                "qty": quantity,
                "discount": discount,
            }],
        })
        return order, order.lines.filtered(lambda line: not line.is_modifier_line)

    def _option_price(self, option, order, quantity):
        if option.price_mode == "fixed_extra":
            return option.extra_price
        if option.price_mode == "product_pricelist":
            return order.pricelist_id._get_product_price(
                option.product_id,
                quantity,
                currency=order.currency_id,
                date=order.date_order,
            )
        return 0

    def _configure(self, parent, selections):
        selection_data = {}
        instructions = []
        child_commands = []
        included_used = {}
        for option, quantity in selections:
            group_line = parent.product_id.product_tmpl_id.modifier_group_line_ids.filtered(
                lambda line: line.modifier_group_id == option.group_id
            )[:1]
            settings = group_line._effective_option(option) if group_line else option
            selection_data[str(option.id)] = quantity
            if option.option_type == "instruction":
                instructions.append({
                    "option_id": option.id,
                    "name": settings.name,
                    "kitchen_name": settings.kitchen_name or settings.name,
                    "group_name": option.group_id.name,
                    "operation": option.operation,
                    "quantity": quantity,
                })
                continue

            group = option.group_id
            used = included_used.get(group.id, 0)
            included_quantity = min(quantity, max(0, group.included_qty - used))
            included_used[group.id] = used + quantity
            paid_quantity = quantity - included_quantity
            for quantity_slice, is_included in (
                (included_quantity, True),
                (paid_quantity, False),
            ):
                if not quantity_slice:
                    continue
                price_unit = 0 if is_included else self._option_price(
                    settings, parent.order_id, quantity_slice * abs(parent.qty)
                )
                child_commands.append(Command.create({
                    "order_id": parent.order_id.id,
                    "product_id": settings.product_id.id,
                    "qty": quantity_slice * parent.qty,
                    "price_unit": price_unit,
                    "price_subtotal": 0,
                    "price_subtotal_incl": 0,
                    "discount": parent.discount,
                    "tax_ids": [Command.set(settings.product_id.taxes_id.ids)],
                    "modifier_option_id": option.id,
                    "modifier_option_name": settings.name,
                    "is_modifier_line": True,
                    "modifier_qty_per_parent": quantity_slice,
                    "modifier_is_included": is_included,
                    "modifier_configuration_key": f"{option.id}:{quantity_slice}:{price_unit}",
                }))

        parent.write({
            "modifier_selection_data": selection_data,
            "modifier_instruction_data": {
                "version": 1,
                "instructions": instructions,
            },
            "modifier_configuration_key": repr(sorted(selection_data.items())),
            "modifier_child_line_ids": child_commands,
        })
        parent.order_id.lines._onchange_amount_line_all()
        parent.order_id._compute_prices()
        return parent.modifier_child_line_ids

    def test_product_specific_modifier_price_product_and_kitchen(self):
        template = self.parent_product.product_tmpl_id
        template.action_sync_modifier_options()
        settings = template.modifier_option_line_ids.filtered(
            lambda line: line.option_id == self.fixed_option
        )
        settings.write({
            "name": "Burger Cheese",
            "kitchen_name": "Burger Cheese Kitchen",
            "product_id": self.pricelist_modifier_product.id,
            "extra_price": 9,
            "default_qty": 0.125,
        })
        order, parent = self._create_order()
        children = self._configure(parent, [(self.fixed_option, 2)])

        order._validate_and_reprice_modifiers()
        self.assertEqual(set(children.product_id.ids), {self.pricelist_modifier_product.id})
        self.assertEqual(set(children.mapped("modifier_option_name")), {"Burger Cheese"})
        paid = children.filtered(lambda line: not line.modifier_is_included)
        self.assertEqual(paid.price_unit, 9)
        self.assertEqual(parent._modifier_kitchen_snapshot()[0]["name"], "Burger Cheese Kitchen")

        paid.price_unit = 5
        with self.assertRaisesRegex(ValidationError, "(?i)modifier price"):
            order._validate_and_reprice_modifiers()

    def test_product_specific_disabled_option_is_rejected(self):
        template = self.parent_product.product_tmpl_id
        template.action_sync_modifier_options()
        settings = template.modifier_option_line_ids.filtered(
            lambda line: line.option_id == self.fixed_option
        )
        settings.available = False
        order, parent = self._create_order()
        self._configure(parent, [(self.fixed_option, 1)])
        with self.assertRaisesRegex(ValidationError, "unavailable modifier option"):
            order._validate_and_reprice_modifiers()

    def test_plain_product_without_modifiers_is_valid(self):
        order, parent = self._create_order(product=self.plain_product)

        order._validate_and_reprice_modifiers()

        self.assertFalse(parent.modifier_child_line_ids)
        self.assertEqual(order.amount_total, 10)

    def test_plain_product_ignores_empty_modifier_instruction_envelope(self):
        order, parent = self._create_order(product=self.plain_product)
        parent.modifier_instruction_data = {
            "version": 1,
            "instructions": [],
        }

        order._validate_and_reprice_modifiers()

        self.assertFalse(parent.modifier_child_line_ids)
        self.assertEqual(order.amount_total, 10)

    def test_plain_product_rejects_real_modifier_instruction_data(self):
        order, parent = self._create_order(product=self.plain_product)
        parent.modifier_instruction_data = {
            "version": 1,
            "instructions": [{
                "option_id": self.instruction_option.id,
                "quantity": 1,
            }],
        }

        with self.assertRaisesRegex(ValidationError, "not enabled"):
            order._validate_and_reprice_modifiers()

    def test_required_group_rejects_missing_selection(self):
        order, _parent = self._create_order()

        with self.assertRaisesRegex(ValidationError, "quantity rules"):
            order._validate_and_reprice_modifiers()

    def test_optional_instruction_has_no_financial_line(self):
        order, parent = self._create_order()
        self._configure(parent, [
            (self.fixed_option, 1),
            (self.instruction_option, 1),
        ])

        order._validate_and_reprice_modifiers()

        self.assertEqual(len(parent.modifier_child_line_ids), 1)
        self.assertTrue(parent.modifier_child_line_ids.modifier_is_included)
        self.assertEqual(parent.modifier_child_line_ids.price_unit, 0)
        self.assertEqual(
            parent.modifier_instruction_data["instructions"][0]["name"],
            "No Onion",
        )
        self.assertEqual(order.amount_total, 20)

    def test_minimum_and_maximum_quantities_are_enforced(self):
        order, parent = self._create_order()
        parent.write({
            "modifier_selection_data": {str(self.fixed_option.id): 5},
            "modifier_instruction_data": {"version": 1, "instructions": []},
        })

        with self.assertRaisesRegex(ValidationError, "quantity rules"):
            order._validate_and_reprice_modifiers()

    def test_included_quantity_and_fixed_extra_are_not_duplicated(self):
        order, parent = self._create_order()
        children = self._configure(parent, [(self.fixed_option, 2)])

        order._validate_and_reprice_modifiers()

        self.assertEqual(len(children), 2)
        self.assertEqual(sorted(children.mapped("price_unit")), [0, 5])
        self.assertEqual(sum(children.mapped("modifier_qty_per_parent")), 2)
        self.assertEqual(order.amount_total, 25)
        self.assertEqual(
            order.amount_total,
            sum(order.lines.mapped("price_subtotal_incl")),
        )

    def test_fractional_modifier_preserves_included_and_paid_slices(self):
        order, parent = self._create_order()
        children = self._configure(parent, [(self.fixed_option, 1.5)])

        order._validate_and_reprice_modifiers()

        self.assertEqual(
            children.filtered("modifier_is_included").modifier_qty_per_parent, 1
        )
        self.assertEqual(
            children.filtered(lambda line: not line.modifier_is_included).modifier_qty_per_parent,
            0.5,
        )
        self.assertEqual(order.amount_total, 22.5)

    def test_fractional_modifier_rejects_incompatible_unit_rounding(self):
        order, parent = self._create_order()
        self._configure(parent, [(self.fixed_option, 1.125)])

        with self.assertRaisesRegex(ValidationError, "unit of measure"):
            order._validate_and_reprice_modifiers()

    def test_modifier_quantity_rejects_more_than_three_decimals(self):
        order, parent = self._create_order()
        self._configure(parent, [(self.fixed_option, 1.1234)])

        with self.assertRaisesRegex(ValidationError, "three decimal places"):
            order._validate_and_reprice_modifiers()

    def test_product_pricelist_price_is_revalidated(self):
        order, parent = self._create_order(pricelist=self.modifier_pricelist)
        children = self._configure(parent, [(self.pricelist_option, 2)])

        order._validate_and_reprice_modifiers()

        paid_child = children.filtered(lambda line: not line.modifier_is_included)
        self.assertEqual(paid_child.price_unit, 7)
        paid_child.price_unit = 999
        with self.assertRaises(ValidationError) as caught:
            order._validate_and_reprice_modifiers()
        message = str(caught.exception)
        self.assertIn("Premium Sauce", message)
        self.assertIn("posmod-%s" % self.pricelist_option.id, message)
        self.assertIn("UAT Burger", message)
        self.assertIn("Order unit price:", message)
        self.assertIn("Current configured unit price:", message)
        self.assertIn("Difference:", message)
        for amount in (999, 7, 992):
            self.assertIn(format_amount(self.env, amount, order.currency_id), message)

        paid_child.price_unit = 7.006
        with self.assertRaises(ValidationError) as caught:
            order._validate_and_reprice_modifiers()
        self.assertIn(
            "Difference: %s" % format_amount(self.env, 0.01, order.currency_id),
            str(caught.exception),
        )

    def test_parent_quantity_controls_modifier_quantity(self):
        order, parent = self._create_order(quantity=2)
        children = self._configure(parent, [(self.fixed_option, 2)])

        order._validate_and_reprice_modifiers()
        self.assertEqual(sum(children.mapped("qty")), 4)

        parent.qty = 3
        with self.assertRaisesRegex(ValidationError, "follow its parent"):
            order._validate_and_reprice_modifiers()
        for child in children:
            child.qty = child.modifier_qty_per_parent * parent.qty
        order._validate_and_reprice_modifiers()

    def test_parent_discount_is_required_on_paid_modifiers(self):
        order, parent = self._create_order(discount=15)
        children = self._configure(parent, [(self.fixed_option, 2)])
        paid_child = children.filtered(lambda line: not line.modifier_is_included)

        order._validate_and_reprice_modifiers()
        self.assertEqual(paid_child.discount, 15)

        paid_child.discount = 0
        with self.assertRaisesRegex(ValidationError, "parent product discount"):
            order._validate_and_reprice_modifiers()

    def test_modifier_taxes_are_revalidated(self):
        tax = self.tax_five_excl
        self.fixed_modifier_product.taxes_id = [Command.set(tax.ids)]
        order, parent = self._create_order()
        children = self._configure(parent, [(self.fixed_option, 2)])

        order._validate_and_reprice_modifiers()
        self.assertEqual(children.tax_ids, tax)

        children.tax_ids = [Command.clear()]
        with self.assertRaisesRegex(ValidationError, "Modifier taxes"):
            order._validate_and_reprice_modifiers()

    def test_fiscal_position_maps_modifier_product_taxes_once(self):
        source_tax = self.tax_five_excl
        destination_tax = self.tax_ten_excl
        self.fixed_modifier_product.taxes_id = [Command.set(source_tax.ids)]
        fiscal_position = self.env["account.fiscal.position"].create({
            "name": "UAT Modifier Fiscal Position",
            "tax_ids": [Command.set(destination_tax.ids)],
        })
        destination_tax.original_tax_ids = [Command.set(source_tax.ids)]
        order, parent = self._create_order()
        order.fiscal_position_id = fiscal_position
        children = self._configure(parent, [(self.fixed_option, 2)])

        order._validate_and_reprice_modifiers()

        self.assertEqual(children.tax_ids, source_tax)
        self.assertEqual(children.tax_ids_after_fiscal_position, destination_tax)

    def test_orphan_modifier_line_is_rejected(self):
        order, _parent = self._create_order(product=self.plain_product)
        orphan = self.env["pos.order.line"].create({
            "order_id": order.id,
            "product_id": self.fixed_modifier_product.id,
            "qty": 1,
            "price_unit": 5,
            "price_subtotal": 5,
            "price_subtotal_incl": 5,
            "is_modifier_line": True,
            "modifier_option_id": self.fixed_option.id,
        })

        with self.assertRaisesRegex(ValidationError, "parent line"):
            orphan._validate_modifier_integrity()

    def test_deleting_parent_cascades_to_modifier_children(self):
        _order, parent = self._create_order()
        children = self._configure(parent, [(self.fixed_option, 2)])
        child_ids = children.ids

        parent.unlink()

        self.assertFalse(self.env["pos.order.line"].browse(child_ids).exists())

    def test_deleting_only_one_modifier_cannot_leave_stale_selection_data(self):
        order, parent = self._create_order()
        children = self._configure(parent, [(self.fixed_option, 2)])
        children.filtered(lambda line: not line.modifier_is_included).unlink()

        with self.assertRaisesRegex(ValidationError, "do not match"):
            order._validate_and_reprice_modifiers()

    def test_refund_keeps_parent_and_modifier_relationships(self):
        order, parent = self._create_order()
        original_children = self._configure(parent, [(self.fixed_option, 2)])
        order._validate_and_reprice_modifiers()

        refund = order._refund()
        refund_parent = refund.lines.filtered(
            lambda line: line.refunded_orderline_id == parent
        )

        self.assertEqual(len(refund_parent), 1)
        self.assertEqual(refund_parent.qty, -1)
        self.assertEqual(len(refund_parent.modifier_child_line_ids), 2)
        self.assertEqual(
            set(refund_parent.modifier_child_line_ids.refunded_orderline_id.ids),
            set(original_children.ids),
        )
        self.assertEqual(refund.amount_total, -25)
        refund._validate_and_reprice_modifiers()

    def test_same_product_with_different_modifiers_keeps_distinct_configurations(self):
        order, _refund = self.create_backend_pos_order({
            "pos_config": self.pos_config_usd,
            "line_data": [
                {"product_id": self.parent_product.id, "qty": 1},
                {"product_id": self.parent_product.id, "qty": 1},
            ],
        })
        first, second = order.lines.sorted("id")
        self._configure(first, [(self.fixed_option, 1)])
        self._configure(second, [(self.pricelist_option, 1)])

        order._validate_and_reprice_modifiers()

        self.assertNotEqual(
            first.modifier_configuration_key,
            second.modifier_configuration_key,
        )

    def test_draft_save_runs_authoritative_modifier_validation(self):
        order, _parent = self._create_order()

        with self.assertRaisesRegex(ValidationError, "quantity rules"):
            order._process_saved_order(draft=True)

    def test_platform_order_is_not_stopped_by_cashier_quantity_rules(self):
        """Delivery platform orders are paid already: limits must not block them."""
        order, _parent = self._create_order()
        with self.assertRaisesRegex(ValidationError, "quantity rules"):
            order._validate_and_reprice_modifiers()
        # Missing required selection (group minimum).
        order.with_context(bs_delivery_core_import=True)._validate_and_reprice_modifiers()

        order, parent = self._create_order()
        self._configure(parent, [(self.fixed_option, 3)])
        self.fixed_option.max_qty = 1
        with self.assertRaisesRegex(ValidationError, "maximum quantity"):
            order._validate_and_reprice_modifiers()
        # Option above its maximum.
        order.with_context(bs_delivery_core_import=True)._validate_and_reprice_modifiers()

    def test_invoicing_omits_free_modifier_but_keeps_paid_modifier(self):
        _order, parent = self._create_order()
        children = self._configure(parent, [(self.fixed_option, 2)])
        lines = parent | children

        tax_base_lines = lines.with_context(invoicing=True)._prepare_tax_base_line_values()

        self.assertEqual(len(tax_base_lines), 2)
