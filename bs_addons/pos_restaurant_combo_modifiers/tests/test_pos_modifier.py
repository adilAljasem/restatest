from types import SimpleNamespace

from lxml import etree
from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import Form, TransactionCase
from odoo.tools import mute_logger
from psycopg2 import IntegrityError


class TestPosModifier(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product_a = cls.env["product.product"].create(
            {
                "name": "Modifier Product A",
                "available_in_pos": True,
            }
        )
        cls.product_b = cls.env["product.product"].create(
            {
                "name": "Modifier Product B",
                "available_in_pos": True,
            }
        )

    def _create_group(self, **values):
        values.setdefault("name", "Test Modifier Group")
        values.setdefault("selection_type", "multiple")
        values.setdefault("max_qty", 2)
        return self.env["pos.modifier.group"].create(values)

    def _create_product_option(self, group, product=None, **values):
        values.update(
            {
                "name": values.get("name", "Product Option"),
                "group_id": group.id,
                "option_type": "product",
                "product_id": product.id if product else False,
            }
        )
        return self.env["pos.modifier.option"].create(values)

    def test_product_form_has_one_expandable_modifier_table(self):
        view = self.env["product.template"].get_view(
            view_id=self.env.ref("point_of_sale.product_template_form_view").id,
            view_type="form",
        )
        root = etree.fromstring(view["arch"].encode())
        page = root.xpath("//page[@name='modifiers']")[0]
        self.assertEqual(
            len(page.xpath(".//field[@name='modifier_group_line_ids']/list")), 1
        )
        self.assertFalse(page.xpath(".//field[@name='modifier_option_line_ids']"))
        self.assertEqual(
            page.xpath(".//field[@name='modifier_group_line_ids']")[0].get("widget"),
            "modifier_group_options",
        )
        self.assertTrue(page.xpath(".//field[@name='modifier_option_editor_changes']"))

    def test_product_form_saves_option_drafts_atomically_and_resets_override(self):
        group = self._create_group()
        option = self._create_product_option(
            group, self.product_a, price_mode="fixed_extra", extra_price=2,
        )
        template = self.env["product.template"].create({
            "name": "Draft Meal", "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": group.id})],
        })
        draft = [{"group_id": group.id, "option_id": option.id,
                  "values": {"extra_price": 4.75, "product_id": self.product_b.id}}]
        self.assertFalse(template.modifier_group_line_ids.option_line_ids)
        template.write({"modifier_option_editor_changes": draft})
        override = template.modifier_group_line_ids.option_line_ids
        self.assertEqual(override.extra_price, 4.75)
        self.assertEqual(override.product_id, self.product_b)
        self.assertEqual(option.extra_price, 2)
        self.assertFalse(template.modifier_option_editor_changes)

        with self.assertRaises(ValidationError), self.cr.savepoint():
            template.write({
                "name": "Invalid Draft Meal",
                "modifier_option_editor_changes": [{
                    "group_id": group.id, "option_id": option.id,
                    "values": {"default_qty": 3, "max_qty": 1},
                }],
            })
        self.assertEqual(template.name, "Draft Meal")
        self.assertEqual(override.extra_price, 4.75)

        template.write({"modifier_option_editor_changes": [
            {"group_id": group.id, "option_id": option.id, "reset": True},
        ]})
        self.assertFalse(template.modifier_group_line_ids.option_line_ids)
        self.assertEqual(template.modifier_group_line_ids.get_options_for_editor()[0]["extra_price"], 2)

    def test_new_group_can_show_shared_options_before_saving(self):
        group = self._create_group()
        option = self._create_product_option(group, self.product_a)
        rows = group.get_options_for_product_editor()
        self.assertEqual([row["id"] for row in rows], [option.id])
        self.assertFalse(rows[0]["overridden"])
        self.assertIn("shared", rows[0])

        template = self.env["product.template"].create({
            "name": "New Draft Meal", "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": group.id})],
            "modifier_option_editor_changes": [{
                "group_id": group.id, "option_id": option.id,
                "values": {"default_qty": 0.375},
            }],
        })
        self.assertEqual(template.modifier_group_line_ids.option_line_ids.default_qty, 0.375)
        self.assertFalse(template.modifier_option_editor_changes)

    def test_product_form_draft_rejects_unlinked_option_and_cashier(self):
        group = self._create_group()
        other_group = self._create_group(name="Other Modifier Group")
        option = self._create_product_option(other_group, self.product_a)
        template = self.env["product.template"].create({
            "name": "Restricted Draft Meal", "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": group.id})],
        })
        draft = [{"group_id": group.id, "option_id": option.id,
                  "values": {"extra_price": 3}}]
        with self.assertRaises(ValidationError), self.cr.savepoint():
            template.write({"modifier_option_editor_changes": draft})
        cashier = self.env["res.users"].create({
            "name": "Draft Cashier", "login": "modifier_draft_cashier",
            "group_ids": [
                Command.link(self.env.ref("base.group_user").id),
                Command.link(self.env.ref("point_of_sale.group_pos_user").id),
            ],
        })
        linked_option = self._create_product_option(group, self.product_b)
        draft[0]["option_id"] = linked_option.id
        with self.assertRaises(AccessError), self.cr.savepoint():
            template.with_user(cashier).write({"modifier_option_editor_changes": draft})
        self.assertFalse(template.modifier_group_line_ids.option_line_ids)

    def test_group_editor_reads_shared_options_and_saves_only_one_product_override(
        self,
    ):
        group = self._create_group()
        option = self._create_product_option(
            group, self.product_a, name="Shared Cheese", price_mode="fixed_extra",
            extra_price=2, default_qty=0.5,
        )
        templates = self.env["product.template"].create([
            {"name": "First Meal", "allow_modifiers": True,
             "modifier_group_line_ids": [(0, 0, {"modifier_group_id": group.id})]},
            {"name": "Second Meal", "allow_modifiers": True,
             "modifier_group_line_ids": [(0, 0, {"modifier_group_id": group.id})]},
        ])
        first, second = templates.sorted("id").modifier_group_line_ids
        self.assertFalse(first.option_line_ids)
        rows = first.get_options_for_editor()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Shared Cheese")
        self.assertFalse(rows[0]["overridden"])
        first.save_option_from_editor(
            option.id, {"name": "First Cheese", "extra_price": 4}
        )
        self.assertEqual(first.option_line_ids.name, "First Cheese")
        self.assertEqual(first.option_line_ids.extra_price, 4)
        self.assertFalse(second.option_line_ids)
        self.assertEqual(option.name, "Shared Cheese")

    def test_group_editor_rejects_unrelated_options_and_cashier_writes(self):
        group = self._create_group()
        other_group = self._create_group(name="Other Group")
        option = self._create_product_option(group, self.product_a)
        unrelated = self._create_product_option(other_group, self.product_b)
        template = self.env["product.template"].create({
            "name": "Restricted Meal", "allow_modifiers": True,
            "modifier_group_line_ids": [
                Command.create({"modifier_group_id": group.id}),
            ],
        })
        line = template.modifier_group_line_ids
        with self.assertRaises(ValidationError):
            line.save_option_from_editor(unrelated.id, {"extra_price": 3})
        cashier = self.env["res.users"].create({
            "name": "Modifier Cashier", "login": "modifier_editor_cashier",
            "group_ids": [
                Command.link(self.env.ref("base.group_user").id),
                Command.link(self.env.ref("point_of_sale.group_pos_user").id),
            ],
        })
        self.assertFalse(line.with_user(cashier).get_options_for_editor()[0]["editable"])
        with self.assertRaises(AccessError):
            line.with_user(cashier).save_option_from_editor(option.id, {"extra_price": 3})
        self.assertFalse(line.option_line_ids)

    def test_modifier_group_can_be_reused_by_products(self):
        group = self._create_group()

        line_model = self.env["pos.product.modifier.group.line"]
        first_line = line_model.create(
            {
                "product_tmpl_id": self.product_a.product_tmpl_id.id,
                "modifier_group_id": group.id,
                "sequence": 20,
                "auto_open": True,
            }
        )
        line_model.create(
            {
                "product_tmpl_id": self.product_b.product_tmpl_id.id,
                "modifier_group_id": group.id,
            }
        )

        self.assertEqual(
            set(group.product_template_ids.ids),
            {self.product_a.product_tmpl_id.id, self.product_b.product_tmpl_id.id},
        )
        first_line.unlink()
        self.assertTrue(group.exists())
        self.assertEqual(group.product_template_ids, self.product_b.product_tmpl_id)

    def test_product_option_edits_are_isolated_and_sync_preserves_them(self):
        group = self._create_group()
        option = self._create_product_option(
            group, self.product_a, name="Shared Cheese", price_mode="fixed_extra",
            extra_price=2, default_qty=0.5,
        )
        parents = self.env["product.template"].create([
            {"name": "First Meal", "allow_modifiers": True,
             "modifier_group_line_ids": [(0, 0, {"modifier_group_id": group.id})]},
            {"name": "Second Meal", "allow_modifiers": True,
             "modifier_group_line_ids": [(0, 0, {"modifier_group_id": group.id})]},
        ])
        parents.action_sync_modifier_options()
        first, second = parents.sorted("id")
        first_option = first.modifier_group_line_ids.option_line_ids
        second_option = second.modifier_group_line_ids.option_line_ids
        self.assertEqual(first_option.option_id, option)
        self.assertEqual(second_option.option_id, option)

        first_option.write({"name": "Extra Cheese for First", "extra_price": 4,
                            "default_qty": 0.125, "product_id": self.product_b.id})
        parents.action_sync_modifier_options()

        self.assertEqual(len(first.modifier_group_line_ids.option_line_ids), 1)
        self.assertEqual(first_option.name, "Extra Cheese for First")
        self.assertEqual(first_option.extra_price, 4)
        self.assertEqual(first_option.default_qty, 0.125)
        self.assertEqual(first_option.product_id, self.product_b)
        self.assertEqual(second_option.name, "Shared Cheese")
        self.assertEqual(second_option.extra_price, 2)
        self.assertEqual(second_option.product_id, self.product_a)
        self.assertEqual(option.name, "Shared Cheese")

        new_option = self.env["pos.modifier.option"].create({
            "name": "No Salt", "group_id": group.id,
            "option_type": "instruction", "operation": "remove",
        })
        parents.action_sync_modifier_options()
        self.assertEqual(len(first.modifier_option_line_ids), 2)
        self.assertEqual(len(second.modifier_option_line_ids), 2)
        self.assertIn(new_option, first.modifier_option_line_ids.option_id)
        self.assertEqual(first_option.name, "Extra Cheese for First")
        with self.assertRaises(ValidationError), self.cr.savepoint():
            first_option.write({"default_qty": 2, "max_qty": 1})

    def test_product_can_have_ordered_modifier_groups(self):
        first_group = self._create_group(name="First Group")
        second_group = self._create_group(name="Second Group")
        template = self.product_a.product_tmpl_id
        template.write(
            {
                "allow_modifiers": True,
                "modifier_group_line_ids": [
                    (0, 0, {"modifier_group_id": second_group.id, "sequence": 20}),
                    (0, 0, {"modifier_group_id": first_group.id, "sequence": 10}),
                ],
            }
        )

        self.assertTrue(template.allow_modifiers)
        self.assertEqual(
            template.modifier_group_line_ids.modifier_group_id,
            first_group | second_group,
        )

    def test_new_product_group_line_defaults_to_current_company(self):
        line_model = self.env["pos.product.modifier.group.line"]

        self.assertEqual(
            line_model.default_get(["company_id"])["company_id"],
            self.env.company.id,
        )

        new_line = line_model.new({
            "product_tmpl_id": self.product_a.product_tmpl_id.id,
        })
        self.assertEqual(new_line.company_id, self.env.company)

    def test_same_group_cannot_be_linked_twice_to_product(self):
        group = self._create_group()
        values = {
            "product_tmpl_id": self.product_a.product_tmpl_id.id,
            "modifier_group_id": group.id,
        }
        self.env["pos.product.modifier.group.line"].create(values)

        with (
            self.assertRaises(IntegrityError),
            mute_logger("odoo.sql_db"),
            self.cr.savepoint(),
        ):
            self.env["pos.product.modifier.group.line"].create(values)

    def test_modifier_product_can_be_hidden_without_disabling_pos(self):
        group = self._create_group()
        self._create_product_option(group, self.product_a)
        template = self.product_a.product_tmpl_id
        template.hide_from_pos_screen = True

        self.assertTrue(template.available_in_pos)
        self.assertTrue(template.is_pos_modifier_product)
        self.assertTrue(template.hide_from_pos_screen)

    def test_single_selection_forces_maximum_quantity_to_one(self):
        group = self._create_group(selection_type="single", max_qty=5)

        self.assertEqual(group.max_qty, 1)

        group.write({"selection_type": "multiple", "max_qty": 4})
        group.write({"selection_type": "single"})
        self.assertEqual(group.max_qty, 1)

        group.write({"max_qty": 9})
        self.assertEqual(group.max_qty, 1)

    def test_required_group_uses_positive_minimum_quantity(self):
        optional_group = self._create_group(min_qty=0)
        required_group = self._create_group(name="Required", min_qty=1)

        self.assertFalse(optional_group.min_qty > 0)
        self.assertTrue(required_group.min_qty > 0)

    def test_minimum_cannot_exceed_maximum(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._create_group(min_qty=3, max_qty=2)

    def test_included_cannot_exceed_maximum(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._create_group(included_qty=3, max_qty=2)

    def test_default_quantity_cannot_exceed_option_maximum(self):
        group = self._create_group()

        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._create_product_option(
                group,
                self.product_a,
                default_qty=2,
                max_qty=1,
            )

    def test_fractional_default_quantity_is_preserved(self):
        group = self._create_group()
        option = self._create_product_option(
            group, self.product_a, default_qty=0.125, max_qty=1
        )

        self.assertAlmostEqual(option.default_qty, 0.125)

    def test_group_option_form_expands_max_for_fractional_default(self):
        group = self._create_group(max_qty=100)
        option = self._create_product_option(group, self.product_a, max_qty=1)

        with Form(option) as form:
            form.default_qty = 1.5
            self.assertEqual(form.max_qty, 2)

        self.assertEqual(option.default_qty, 1.5)
        self.assertEqual(option.max_qty, 2)
        self.assertEqual(group.max_qty, 100)

    def test_modifier_option_loads_into_pos_without_missing_currency_helper(self):
        group = self._create_group()
        option = self._create_product_option(
            group, self.product_a, extra_price=5
        )
        config = SimpleNamespace(currency_id=self.env.company.currency_id)

        rows = option._load_pos_data_read(option, config)

        self.assertEqual(rows[0]["extra_price"], 5)

    def test_product_cannot_be_duplicated_in_group(self):
        group = self._create_group()
        self._create_product_option(group, self.product_a)

        with (
            self.assertRaises(IntegrityError),
            mute_logger("odoo.sql_db"),
            self.cr.savepoint(),
        ):
            self._create_product_option(group, self.product_a, name="Duplicate")

    def test_same_product_can_be_used_in_different_groups(self):
        first_group = self._create_group(name="First Group")
        second_group = self._create_group(name="Second Group")

        self._create_product_option(first_group, self.product_a)
        self._create_product_option(second_group, self.product_a)

    def test_product_option_requires_product(self):
        group = self._create_group()

        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._create_product_option(group)

    def test_instruction_does_not_require_product_and_has_zero_price(self):
        group = self._create_group()
        instruction = self.env["pos.modifier.option"].create(
            {
                "name": "No Onions",
                "group_id": group.id,
                "option_type": "instruction",
                "operation": "note",
                "price_mode": "included",
                "extra_price": 0.0,
            }
        )

        self.assertFalse(instruction.product_id)
        self.assertEqual(instruction.extra_price, 0.0)

    def test_instruction_rejects_nonzero_price(self):
        group = self._create_group()

        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env["pos.modifier.option"].create(
                {
                    "name": "Paid Instruction",
                    "group_id": group.id,
                    "option_type": "instruction",
                    "price_mode": "fixed_extra",
                    "extra_price": 1.0,
                }
            )

    def test_modifier_price_modes_match_pos_pricing_contract(self):
        price_modes = dict(
            self.env["pos.modifier.option"]._fields["price_mode"].selection
        )

        self.assertEqual(
            set(price_modes),
            {"included", "fixed_extra", "product_pricelist"},
        )

    def test_modifier_name_snapshot_does_not_follow_later_option_rename(self):
        group = self._create_group()
        option = self._create_product_option(
            group,
            self.product_a,
            name="Original Receipt Name",
        )
        order_line = self.env["pos.order.line"].new({
            "modifier_option_id": option.id,
            "modifier_option_name": option.name,
            "is_modifier_line": True,
        })

        option.name = "Renamed Configuration Option"

        self.assertEqual(order_line.modifier_option_name, "Original Receipt Name")
