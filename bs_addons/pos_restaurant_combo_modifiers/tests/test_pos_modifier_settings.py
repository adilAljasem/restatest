from pathlib import Path

from odoo import Command
from odoo.tests.common import TransactionCase


class TestPosModifierSettings(TransactionCase):
    def _new_config(self, enabled=True):
        return self.env["pos.config"].new({
            "company_id": self.env.company.id,
            "enable_product_modifiers": enabled,
        })

    def test_pos_settings_defaults_and_standard_config_loader(self):
        defaults = self.env["pos.config"].default_get([
            "enable_product_modifiers",
            "modifier_display_type",
            "show_modifier_images",
        ])

        self.assertTrue(defaults["enable_product_modifiers"])
        self.assertEqual(defaults["modifier_display_type"], "grid")
        self.assertFalse(defaults["show_modifier_images"])

        loaded_fields = self.env["pos.config"]._load_pos_data_fields(
            self._new_config()
        )
        self.assertFalse(
            loaded_fields,
            "Keep Odoo's empty-field sentinel so pos.config.read() loads the "
            "standard fields and the modifier settings together.",
        )

    def test_settings_view_exposes_only_the_three_modifier_settings(self):
        view = self.env.ref(
            "pos_restaurant_combo_modifiers.res_config_settings_view_form_pos_modifiers"
        )

        self.assertEqual(view.model, "res.config.settings")
        for field_name in (
            "pos_enable_product_modifiers",
            "pos_modifier_display_type",
            "pos_show_modifier_images",
        ):
            self.assertIn(field_name, view.arch_db)

    def test_disabled_modifiers_do_not_load_master_data(self):
        config = self._new_config(enabled=False)
        data = {"product.template": [{"id": 1}]}

        for model_name in (
            "pos.product.modifier.group.line",
            "pos.modifier.group",
            "pos.modifier.option",
        ):
            self.assertFalse(
                self.env[model_name]._load_pos_data_domain(data, config),
                model_name,
            )

    def test_hidden_modifier_products_are_not_in_the_initial_product_domain(self):
        domain = self.env["product.template"]._load_pos_data_domain(
            {}, self._new_config()
        )

        self.assertIn(("hide_from_pos_screen", "=", False), domain)

    def test_incremental_load_restores_internal_modifier_product(self):
        config = self.env["pos.config"].create({
            "name": "Modifier Loader Test",
            "enable_product_modifiers": True,
        })
        parent_product = self.env["product.product"].create({
            "name": "Modifier Loader Parent",
            "available_in_pos": True,
            "allow_modifiers": True,
            "company_id": self.env.company.id,
        })
        modifier_product = self.env["product.product"].create({
            "name": "Modifier Loader Hidden Option",
            "available_in_pos": False,
            "sale_ok": False,
            "hide_from_pos_screen": True,
            "company_id": self.env.company.id,
        })
        group = self.env["pos.modifier.group"].create({
            "name": "Modifier Loader Group",
            "selection_type": "multiple",
            "max_qty": 2,
            "company_id": self.env.company.id,
        })
        self.env["pos.modifier.option"].create({
            "name": "Modifier Loader Option",
            "group_id": group.id,
            "option_type": "product",
            "operation": "add",
            "product_id": modifier_product.id,
            "price_mode": "fixed_extra",
            "extra_price": 5,
        })
        self.env["pos.product.modifier.group.line"].create({
            "product_tmpl_id": parent_product.product_tmpl_id.id,
            "modifier_group_id": group.id,
            "company_id": self.env.company.id,
        })

        response = {
            "pos.modifier.option": [{
                "id": 1,
                "option_type": "product",
                "product_id": modifier_product.id,
            }],
            "product.product": [],
            "product.template": [],
            "account.tax": [],
        }
        self.env[
            "pos.modifier.option"
        ]._append_referenced_products_to_pos_data(
            response,
            [],
            config,
        )

        self.assertIn(
            modifier_product.id,
            {product["id"] for product in response["product.product"]},
        )
        self.assertIn(
            modifier_product.product_tmpl_id.id,
            {template["id"] for template in response["product.template"]},
        )

    def test_loader_domains_follow_current_products_company_and_active_state(self):
        config = self._new_config()
        company_id = self.env.company.id
        line_model = self.env["pos.product.modifier.group.line"]
        group_model = self.env["pos.modifier.group"]
        option_model = self.env["pos.modifier.option"]

        line_domain = line_model._load_pos_data_domain(
            {
                "product.template": [
                    {
                        "id": 11,
                        "allow_modifiers": True,
                        "is_pos_modifier_product": False,
                    },
                    {
                        "id": 12,
                        "allow_modifiers": True,
                        "is_pos_modifier_product": False,
                    },
                    {
                        "id": 13,
                        "allow_modifiers": True,
                        "is_pos_modifier_product": True,
                    },
                    {"id": 14, "allow_modifiers": False},
                ]
            },
            config,
        )
        self.assertIn(("product_tmpl_id", "in", [11, 12, 13]), line_domain)
        self.assertIn(("modifier_group_id.active", "=", True), line_domain)
        self.assertIn(("company_id", "=", company_id), line_domain)

        group_domain = group_model._load_pos_data_domain(
            {
                "pos.product.modifier.group.line": [
                    {"id": 21, "modifier_group_id": 31},
                    {"id": 22, "modifier_group_id": 32},
                ]
            },
            config,
        )
        loaded_group_ids = next(
            value for field, operator, value in group_domain
            if field == "id" and operator == "in"
        )
        self.assertEqual(set(loaded_group_ids), {31, 32})
        self.assertIn(("active", "=", True), group_domain)
        self.assertIn(("company_id", "=", company_id), group_domain)

        option_domain = option_model._load_pos_data_domain(
            {"pos.modifier.group": [{"id": 31}, {"id": 32}]}, config
        )
        self.assertIn(("group_id", "in", [31, 32]), option_domain)
        self.assertIn(("active", "=", True), option_domain)
        self.assertIn(("company_id", "=", company_id), option_domain)

    def test_variant_used_as_modifier_keeps_its_own_groups_in_pos(self):
        attribute = self.env["product.attribute"].create({
            "name": "Modifier Loader Size",
            "create_variant": "always",
            "value_ids": [
                Command.create({"name": "Small"}),
                Command.create({"name": "Large"}),
            ],
        })
        template = self.env["product.template"].create({
            "name": "Modifier Loader Dual-Use Product",
            "available_in_pos": True,
            "allow_modifiers": True,
            "attribute_line_ids": [Command.create({
                "attribute_id": attribute.id,
                "value_ids": [Command.set(attribute.value_ids.ids)],
            })],
        })
        option_group = self.env["pos.modifier.group"].create({
            "name": "Modifier Loader Other Product Group",
            "max_qty": 1,
        })
        self.env["pos.modifier.option"].create({
            "name": "Modifier Loader Variant Option",
            "group_id": option_group.id,
            "option_type": "product",
            "product_id": template.product_variant_ids[0].id,
        })
        own_group = self.env["pos.modifier.group"].create({
            "name": "Modifier Loader Own Group",
            "max_qty": 1,
        })
        own_line = self.env["pos.product.modifier.group.line"].create({
            "product_tmpl_id": template.id,
            "modifier_group_id": own_group.id,
        })

        self.assertEqual(len(template.product_variant_ids), 2)
        self.assertTrue(template.is_pos_modifier_product)
        domain = self.env["pos.product.modifier.group.line"]._load_pos_data_domain(
            {"product.template": [{
                "id": template.id,
                "allow_modifiers": template.allow_modifiers,
                "is_pos_modifier_product": template.is_pos_modifier_product,
            }]},
            self._new_config(),
        )
        self.assertIn(
            own_line,
            self.env["pos.product.modifier.group.line"].search(domain),
        )

    def test_pos_payload_omits_redundant_scope_fields(self):
        config = self._new_config()
        for model_name in (
            "pos.product.modifier.group.line",
            "pos.modifier.group",
            "pos.modifier.option",
        ):
            fields_to_load = self.env[model_name]._load_pos_data_fields(config)
            self.assertNotIn("company_id", fields_to_load, model_name)
            self.assertNotIn("active", fields_to_load, model_name)

    def test_cashier_and_manager_master_data_permissions(self):
        for suffix in (
            "pos_modifier_group",
            "pos_modifier_option",
            "pos_product_modifier_group_line",
        ):
            cashier_acl = self.env.ref(
                f"pos_restaurant_combo_modifiers.access_{suffix}_user"
            )
            manager_acl = self.env.ref(
                f"pos_restaurant_combo_modifiers.access_{suffix}_manager"
            )
            self.assertTrue(cashier_acl.perm_read)
            self.assertFalse(cashier_acl.perm_write)
            self.assertFalse(cashier_acl.perm_create)
            self.assertFalse(cashier_acl.perm_unlink)
            self.assertTrue(manager_acl.perm_read)
            self.assertTrue(manager_acl.perm_write)
            self.assertTrue(manager_acl.perm_create)
            self.assertTrue(manager_acl.perm_unlink)

    def test_multi_company_rules_are_mandatory(self):
        for rule_xmlid in (
            "pos_modifier_group_company_rule",
            "pos_modifier_option_company_rule",
            "pos_product_modifier_group_line_company_rule",
        ):
            rule = self.env.ref(f"pos_restaurant_combo_modifiers.{rule_xmlid}")
            self.assertIn("('company_id', 'in', company_ids)", rule.domain_force)
            self.assertNotIn("('company_id', '=', False)", rule.domain_force)

    def test_relation_fields_used_by_loader_are_indexed(self):
        self.assertEqual(
            self.env["pos.modifier.option"]._fields["product_id"].index,
            "btree_not_null",
        )
        self.assertEqual(
            self.env["pos.order.line"]._fields["modifier_option_id"].index,
            "btree_not_null",
        )

    def test_configurator_selection_path_has_no_rpc_or_manual_dom_access(self):
        module_path = Path(__file__).parents[1]
        source_paths = [
            module_path / "static/src/app/services/pos_store.js",
            module_path
            / "static/src/app/components/popups/combo_modifier_configurator_popup"
            / "combo_modifier_configurator_popup.js",
        ]
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in source_paths
        )

        for forbidden in (
            "document.querySelector",
            "innerHTML",
            "addEventListener",
            "this.orm",
            "this.rpc",
            "rpc(",
        ):
            self.assertNotIn(forbidden, source)
