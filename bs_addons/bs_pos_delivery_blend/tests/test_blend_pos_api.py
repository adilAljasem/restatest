import json
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessDenied
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.bs_pos_delivery_core.exceptions import DeliveryAuthenticationError

from ..services.auth import BlendAuthService
from ..services.pos_api import BlendPOSAPIService
from ..services.exceptions import BlendAPIError, BlendAuthenticationError


@tagged("post_install", "-at_install")
class TestBlendPOSAPI(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.execution_user = new_test_user(
            cls.env,
            login="blend-selected-odoo-user",
            password="SelectedUser#123",
            groups="base.group_user,point_of_sale.group_pos_user,bs_pos_delivery_core.group_delivery_operator",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.pos_config = cls.env["pos.config"].create({
            "name": "Blend API Test Branch",
            "blend_enabled": True,
            "blend_branch_id": "BRANCH-API-01",
            "blend_brand_id": "BRAND-API-01",
            "blend_inbound_token": "secret-blend-token-123",
        })
        cls.provider = cls.env.ref("bs_pos_delivery_core.provider_blend")
        cls.connection = cls.env["bs.delivery.connection"].create({
            "name": "Blend Connection API",
            "provider_id": cls.provider.id,
            "company_id": cls.company.id,
            "state": "connected",
            "webhook_secret": "secret-blend-token-123",
            "pos_config_ids": [Command.set(cls.pos_config.ids)],
        })
        cls.product = cls.env["product.product"].with_context(blend_inbound_sync=True).create({
            "name": "Burger API Test",
            "available_in_pos": True,
            "list_price": 25.0,
            "blend_external_id": "BURGER-01",
        })

    def test_auth_service_bearer_token(self):
        auth = BlendAuthService(self.env)
        # Pre-shared token
        configs = auth.authorized_configs("Bearer secret-blend-token-123")
        self.assertIn(self.pos_config, configs)

        # Invalid token raises error
        with self.assertRaises(BlendAuthenticationError):
            auth.authorized_configs("Bearer wrong-token")

    def test_auth_service_login_token_rotation(self):
        auth = BlendAuthService(self.env)
        token = self.pos_config._blend_issue_login_token()
        self.assertTrue(token)
        self.assertTrue(self.pos_config.blend_login_token_hash)

        # Authenticate with issued login token
        configs = auth.authorized_configs("Bearer %s" % token)
        self.assertIn(self.pos_config, configs)

        # Revoke token
        self.pos_config.action_revoke_blend_login_token()
        self.assertFalse(self.pos_config.blend_login_token_hash)

    def _configure_basic_login(self):
        self.connection.write({
            "auth_scheme": "basic",
            "username": "blend-connection-login",
            "password": "Connection#&=K)P^\u00e9",
            "blend_execution_user_id": self.execution_user.id,
            "webhook_secret": False,
            "blend_outbound_webhook_url": "https://blend.example.test/webhook",
        })
        self.pos_config.write({
            "blend_inbound_token": False,
            "blend_login_user_id": False,
        })
        # Exercise the persisted encrypted password, not just its create cache.
        self.env.flush_all()
        self.env.invalidate_all()
        return BlendAuthService(self.env)

    def test_connection_login_token_scope_and_rotation(self):
        auth = self._configure_basic_login()
        self.assertTrue(self.connection._blend_validate_configuration())
        other_config = self.env["pos.config"].create({
            "name": "Other Connection Branch",
            "blend_branch_id": "BRANCH-OTHER-LOGIN",
        })
        other_connection = self.env["bs.delivery.connection"].create({
            "name": "Other Connection",
            "provider_id": self.provider.id,
            "pos_config_ids": [Command.set(other_config.ids)],
            "state": "connected",
            "webhook_secret": "unrelated-connection-token",
        })
        store = self.connection._blend_store_for_config(self.pos_config)
        other_store = other_connection._blend_store_for_config(other_config)
        token = auth.login_connection(self.connection.username, self.connection.password)
        self.assertNotEqual(token, self.connection.blend_login_token_hash)
        self.assertTrue(self.connection.blend_login_token_issued_at)
        self.assertFalse(self.connection.webhook_secret)
        self.assertEqual(auth.authorized_configs("Bearer " + token), self.pos_config)
        self.assertEqual(auth.authorized_menu_scope("Bearer " + token), (self.pos_config, self.connection))
        self.assertEqual(auth.authorized_stores(store | other_store, "Bearer " + token), store)
        with self.assertRaises(BlendAuthenticationError):
            auth.authorized_stores(other_store, "Bearer " + token)
        adapter = self.env["bs.delivery.adapter.blend"]
        body = json.dumps({"branch_id": self.pos_config.blend_branch_id}).encode()
        self.assertTrue(adapter.verify_webhook_signature(
            self.connection, raw_body=body, headers={"Authorization": "Bearer " + token},
        ))
        rotated = auth.login_connection(self.connection.username, self.connection.password)
        self.assertNotEqual(token, rotated)
        with self.assertRaises(BlendAuthenticationError):
            auth.authorized_configs("Bearer " + token)
        with self.assertRaises(DeliveryAuthenticationError):
            adapter.verify_webhook_signature(
                self.connection, raw_body=body, headers={"Authorization": "Bearer " + token},
            )
        self.assertEqual(auth.authorized_configs("Bearer " + rotated), self.pos_config)
        self.connection.action_revoke_blend_login_token()
        with self.assertRaises(BlendAuthenticationError):
            auth.authorized_configs("Bearer " + rotated)

    def test_connection_login_rejects_wrong_password_and_disabled_connection(self):
        auth = self._configure_basic_login()
        with self.assertRaises(AccessDenied):
            auth.login_connection(self.connection.username, "incorrect-password")
        token = auth.login_connection(self.connection.username, self.connection.password)
        self.connection.state = "disabled"
        self.assertFalse(self.connection.blend_login_token_hash)
        with self.assertRaises(AccessDenied):
            auth.login_connection(self.connection.username, self.connection.password)
        with self.assertRaises(BlendAuthenticationError):
            auth.authorized_configs("Bearer " + token)

    def test_connection_login_rejects_ambiguous_credentials(self):
        auth = self._configure_basic_login()
        other_config = self.env["pos.config"].create({
            "name": "Duplicate Login Branch",
            "blend_branch_id": "BRANCH-DUPLICATE-LOGIN",
        })
        self.env["bs.delivery.connection"].create({
            "name": "Duplicate Login Connection",
            "provider_id": self.provider.id,
            "pos_config_ids": [Command.set(other_config.ids)],
            "state": "connected",
            "auth_scheme": "basic",
            "username": self.connection.username,
            "password": self.connection.password,
        })
        with self.assertRaises(AccessDenied):
            auth.login_connection(self.connection.username, self.connection.password)

    def test_connection_login_revoked_on_credential_changes(self):
        for values in (
            {"username": "changed-connection-login"},
            {"password": "changed-password"},
            {"auth_scheme": "bearer"},
            {"active": False},
        ):
            with self.subTest(values=tuple(values)):
                auth = self._configure_basic_login()
                token = auth.login_connection(self.connection.username, self.connection.password)
                self.connection.write(values)
                self.assertFalse(self.connection.blend_login_token_hash)
                with self.assertRaises(BlendAuthenticationError):
                    auth.authorized_configs("Bearer " + token)

    def test_connection_login_preserves_preshared_token_and_masked_password(self):
        auth = self._configure_basic_login()
        self.connection.webhook_secret = "existing-preshared-token"
        token = auth.login_connection(self.connection.username, self.connection.password)
        self.connection.write({"password": "********"})
        self.assertEqual(auth.authorized_configs("Bearer " + token), self.pos_config)
        self.assertEqual(
            auth.authorized_configs("Bearer existing-preshared-token"), self.pos_config,
        )

    def test_odoo_user_selector_login_and_password_change(self):
        self.connection.write({
            "blend_login_method": "odoo_user",
            "blend_login_user_id": self.execution_user.id,
        })
        self.assertEqual(self.connection.auth_scheme, "basic")
        self.assertEqual(self.connection.blend_login_username, self.execution_user.login)
        auth = BlendAuthService(self.env)
        token = auth.login_connection(self.execution_user.login, "SelectedUser#123")
        self.assertEqual(auth.authorized_configs("Bearer " + token), self.pos_config)
        execution_env = auth.execution_environment(self.pos_config, "Bearer " + token)
        self.assertEqual(execution_env.uid, self.execution_user.id)
        self.assertFalse(execution_env.su)
        self.assertEqual(execution_env.context["allowed_company_ids"], self.company.ids)
        with self.assertRaises(BlendAuthenticationError):
            auth.authorized_configs("Bearer secret-blend-token-123")
        self.execution_user.password = "ChangedSelectedUser#123"
        with self.assertRaises(BlendAuthenticationError):
            auth.authorized_configs("Bearer " + token)
        with self.assertRaises(AccessDenied):
            auth.login_connection(self.execution_user.login, "SelectedUser#123")
        self.assertTrue(auth.login_connection(self.execution_user.login, "ChangedSelectedUser#123"))

    def test_odoo_user_selector_revokes_previous_user_and_pos_tokens(self):
        legacy_token = self.pos_config._blend_issue_login_token()
        self.connection.write({
            "blend_login_method": "odoo_user",
            "blend_login_user_id": self.execution_user.id,
        })
        auth = BlendAuthService(self.env)
        with self.assertRaises(BlendAuthenticationError):
            auth.authorized_configs("Bearer " + legacy_token)
        token = auth.login_connection(self.execution_user.login, "SelectedUser#123")
        self.connection.blend_login_user_id = self.env.user
        with self.assertRaises(BlendAuthenticationError):
            auth.authorized_configs("Bearer " + token)
        self.assertFalse(auth.legacy_login_configs(self.pos_config.blend_login_user_id.id))

    def test_odoo_user_selector_rejects_inactive_or_unqualified_user(self):
        self.connection.write({
            "blend_login_method": "odoo_user",
            "blend_login_user_id": self.execution_user.id,
        })
        auth = BlendAuthService(self.env)
        token = auth.login_connection(self.execution_user.login, "SelectedUser#123")
        self.execution_user.active = False
        with self.assertRaises(AccessDenied):
            auth.login_connection(self.execution_user.login, "SelectedUser#123")
        with self.assertRaises(BlendAuthenticationError):
            auth.authorized_configs("Bearer " + token)
        self.connection.write({
            "blend_login_method": "credentials",
            "username": "unqualified-execution-user",
            "password": "Connection#123",
            "blend_execution_user_id": self.env.ref("base.public_user").id,
        })
        with self.assertRaises(BlendAPIError) as denied:
            auth.login_connection("unqualified-execution-user", "Connection#123")
        self.assertEqual(denied.exception.status_code, 403)

    def test_queued_operations_use_selected_execution_user(self):
        self._configure_basic_login()
        binding, _created = self.env["bs.delivery.order.binding"].sudo().get_or_create(
            self.connection, "BLEND-EXECUTION-QUEUE", {},
        )
        job, _created = self.env["bs.delivery.outbox"].sudo().enqueue(
            self.connection, "update_order", binding, status_value="preparing",
        )
        adapter = self.env["bs.delivery.adapter.blend"]
        with patch.object(type(adapter), "update_order", autospec=True, return_value={"ok": True}) as dispatch:
            self.assertEqual(job._dispatch_operation(), {"ok": True})
        dispatch.assert_called_once()
        dispatched_connection = dispatch.call_args.args[1]
        self.assertEqual(dispatched_connection.env.uid, self.execution_user.id)
        self.assertEqual(dispatched_connection.env.context["allowed_company_ids"], self.company.ids)

    def test_menu_scope_rejects_legacy_token_spanning_connections(self):
        self.connection.webhook_secret = "first-connection-only"
        self.pos_config.blend_inbound_token = "shared-legacy-token"
        other_config = self.env["pos.config"].create({
            "name": "Other Blend Branch",
            "blend_enabled": True,
            "blend_branch_id": "BRANCH-API-02",
            "blend_brand_id": "BRAND-API-01",
            "blend_inbound_token": "shared-legacy-token",
        })
        self.env["bs.delivery.connection"].create({
            "name": "Other Blend Connection",
            "provider_id": self.provider.id,
            "company_id": self.company.id,
            "state": "connected",
            "webhook_secret": "second-connection-only",
            "pos_config_ids": [Command.set(other_config.ids)],
        })
        with self.assertRaises(BlendAPIError) as caught:
            BlendAuthService(self.env).authorized_menu_scope("Bearer shared-legacy-token")
        self.assertEqual(caught.exception.status_code, 409)

    def test_get_branches_api(self):
        service = BlendPOSAPIService(self.env, self.pos_config)
        branches_data = service.get_branches()
        self.assertIn("branches", branches_data)
        self.assertEqual(len(branches_data["branches"]), 1)
        self.assertEqual(branches_data["branches"][0]["id"], "BRANCH-API-01")
        self.assertEqual(branches_data["branches"][0]["brand_id"], "BRAND-API-01")

    def test_get_menu_api(self):
        pricelist = self.env["product.pricelist"].create({
            "name": "Blend Menu Prices",
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
            "item_ids": [Command.create({
                "applied_on": "0_product_variant",
                "product_id": self.product.id,
                "compute_price": "fixed",
                "fixed_price": 17.5,
            })],
        })
        self.connection.menu_pricelist_id = pricelist
        self.product.blend_external_id = str(self.product.id)
        service = BlendPOSAPIService(self.env, self.pos_config)
        menu_data = service.get_menu(
            page=1, per=10, filters={"product_id": str(self.product.id)}
        )
        self.assertIn("categories", menu_data)
        self.assertIn("pagination", menu_data)
        self.assertEqual(menu_data["pagination"]["current"], 1)
        products = [
            product for category in menu_data["categories"]
            for product in category["products"]
        ]
        burger = next(product for product in products if product["id"] == self.product.id)
        self.assertEqual(burger["price"], 17.5)
        self.assertEqual(set(burger), {
            "id", "name", "description", "stock", "status", "price",
            "image_url", "available_branches", "modifier_groups",
        })

    def test_menu_paginates_products_inside_one_category(self):
        self.connection.menu_pricelist_id = self.env["product.pricelist"].create({
            "name": "Blend Paginated Menu",
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
        })
        category = self.env["pos.category"].create({"name": "Paginated Burgers"})
        products = self.env["product.template"].with_context(
            blend_inbound_sync=True
        ).create([{
            "name": "Paginated Burger %s" % index,
            "available_in_pos": True,
            "delivery_available": True,
            "pos_categ_ids": [Command.set(category.ids)],
        } for index in range(27)])

        service = BlendPOSAPIService(self.env, self.pos_config)
        filters = {"category_id": str(category.id)}
        first = service.get_menu(page=1, per=25, filters=filters)
        second = service.get_menu(page=2, per=25, filters=filters)
        first_ids = [row["id"] for group in first["categories"] for row in group["products"]]
        second_ids = [row["id"] for group in second["categories"] for row in group["products"]]

        self.assertEqual(len(first_ids), 25)
        self.assertEqual(len(second_ids), 2)
        self.assertEqual(set(first_ids + second_ids), set(products.ids))
        self.assertEqual(first["pagination"]["total"], 2)
        self.assertEqual(second["pagination"]["total"], 2)
        self.assertEqual((first["pagination"]["from"], first["pagination"]["to"]), (1, 2))
        self.assertEqual((second["pagination"]["from"], second["pagination"]["to"]), (1, 2))
        self.assertEqual(first["pagination"]["current"], 1)
        self.assertEqual(second["pagination"]["current"], 2)

    def test_menu_pagination_keeps_category_groups_at_page_boundary(self):
        self.connection.menu_pricelist_id = self.env["product.pricelist"].create({
            "name": "Blend Grouped Paginated Menu",
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
        })
        first_category, second_category = self.env["pos.category"].create([
            {"name": "First Page Category"},
            {"name": "Split Page Category"},
        ])
        self.pos_config.write({
            "limit_categories": True,
            "iface_available_categ_ids": [Command.set((first_category | second_category).ids)],
        })
        products = self.env["product.template"].with_context(blend_inbound_sync=True)
        products.create([{
            "name": "First Page Product %s" % index,
            "available_in_pos": True,
            "pos_categ_ids": [Command.set(first_category.ids)],
        } for index in range(20)])
        products.create([{
            "name": "Split Page Product %s" % index,
            "available_in_pos": True,
            "pos_categ_ids": [Command.set(second_category.ids)],
        } for index in range(7)])

        service = BlendPOSAPIService(self.env, self.pos_config)
        first = service.get_menu(page=1, per=25)
        second = service.get_menu(page=2, per=25)
        self.assertEqual(
            [(category["id"], len(category["products"])) for category in first["categories"]],
            [(first_category.id, 20), (second_category.id, 5)],
        )
        self.assertEqual(
            [(category["id"], len(category["products"])) for category in second["categories"]],
            [(second_category.id, 2)],
        )
        self.assertEqual(second["pagination"]["total"], 2)

    def test_menu_excludes_closed_and_archived_products_without_hiding_delivery_products(self):
        self.connection.menu_pricelist_id = self.env["product.pricelist"].create({
            "name": "Blend Delivery Catalogue",
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
        })
        products = self.env["product.template"].with_context(blend_inbound_sync=True)
        dine_in = products.create({
            "name": self.product.name,
            "available_in_pos": True,
            "delivery_available": False,
        })
        archived = products.create({
            "name": "Archived Delivery Item", "available_in_pos": True,
        })
        archived.active = False
        removed = products.create({
            "name": "Removed Blend Item", "available_in_pos": True,
            "blend_external_id": "987654321", "blend_menu_active": False,
        })
        closed = products.create({
            "name": "Closed Blend Item", "available_in_pos": True,
            "blend_external_id": "987654322", "blend_is_available": False,
        })
        delivery = products.create({
            "name": "Unbound Delivery Item", "available_in_pos": True,
            "delivery_available": True,
        })
        out_of_stock = products.create({
            "name": "Temporarily Out of Stock", "available_in_pos": True,
            "delivery_available": True, "is_storable": True,
        })

        service = BlendPOSAPIService(self.env, self.pos_config)

        def menu_ids(product):
            response = service.get_menu(filters={"product_id": str(product.id)})
            return [
                row["id"] for category in response["categories"]
                for row in category["products"]
            ]

        self.assertEqual(menu_ids(dine_in), [])
        self.assertEqual(menu_ids(archived), [])
        self.assertEqual(menu_ids(removed), [])
        self.assertEqual(menu_ids(closed), [])
        self.assertEqual(menu_ids(delivery), [delivery.id])
        self.assertEqual(menu_ids(delivery), [delivery.id])
        stock_response = service.get_menu(filters={"product_id": str(out_of_stock.id)})
        stock_rows = [
            row for category in stock_response["categories"]
            for row in category["products"]
        ]
        self.assertEqual(len(stock_rows), 1)
        # Odoo stock never closes a delivery item.
        self.assertTrue(stock_rows[0]["available_branches"][0]["is_stock"])
        self.assertTrue(stock_rows[0]["status"])

    def _menu_option_id(self, product, option):
        group_line = product.product_tmpl_id.modifier_group_line_ids.filtered(
            lambda line: line.modifier_group_id == option.group_id
        )
        identity = self.env["bs.blend.modifier.identity"].sudo()._ids_for(
            [(group_line.id, option.id)]
        )[(group_line.id, option.id)]
        return BlendPOSAPIService.MODIFIER_ID_BASE + identity

    def test_hidden_pos_product_is_in_blend_menu_but_blend_option_is_not(self):
        self.connection.menu_pricelist_id = self.env["product.pricelist"].create({
            "name": "Blend Hidden POS Product Catalogue",
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
        })
        products = self.env["product.template"].with_context(blend_inbound_sync=True)
        hidden_product = products.create({
            "name": "Delivery Sauce",
            "available_in_pos": True,
            "delivery_available": True,
            "hide_from_pos_screen": True,
        })
        blend_option = products.create({
            "name": "Blend-only Topping",
            "available_in_pos": True,
            "delivery_available": True,
            "hide_from_pos_screen": True,
            "blend_is_option": True,
        })
        pos_domain = products._load_pos_data_domain({}, self.pos_config)
        self.assertNotIn(hidden_product, products.search(pos_domain))

        service = BlendPOSAPIService(self.env, self.pos_config)
        hidden_menu = service.get_menu(filters={"product_id": str(hidden_product.id)})
        hidden_ids = [
            row["id"] for category in hidden_menu["categories"]
            for row in category["products"]
        ]
        self.assertEqual(hidden_ids, [hidden_product.id])

        option_menu = service.get_menu(filters={"product_id": str(blend_option.id)})
        self.assertFalse(any(category["products"] for category in option_menu["categories"]))

    def test_menu_excludes_closed_and_archived_modifier_options(self):
        self.connection.menu_pricelist_id = self.env["product.pricelist"].create({
            "name": "Blend Modifier Catalogue",
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
        })
        products = self.env["product.product"].with_context(blend_inbound_sync=True)
        available_product = products.create({
            "name": "Available Extra", "available_in_pos": True,
        })
        closed_product = products.create({
            "name": "Dine In Extra", "available_in_pos": True,
            "delivery_available": False,
        })
        archived_product = products.create({
            "name": "Archived Extra", "available_in_pos": True,
        })
        archived_product.product_tmpl_id.active = False
        group = self.env["pos.modifier.group"].create({
            "name": "Extras", "company_id": self.company.id,
            "selection_type": "multiple", "max_qty": 4,
        })
        options = self.env["pos.modifier.option"].with_context(blend_inbound_sync=True)
        available_option = options.create({
            "name": "Available Extra", "group_id": group.id,
            "option_type": "product", "product_id": available_product.id,
        })
        closed_option = options.create({
            "name": "Dine In Extra", "group_id": group.id,
            "option_type": "product", "product_id": closed_product.id,
        })
        archived_product_option = options.create({
            "name": "Archived Extra", "group_id": group.id,
            "option_type": "product", "product_id": archived_product.id,
        })
        inactive_option = options.create({
            "name": "Disabled Extra", "group_id": group.id,
            "option_type": "instruction", "active": False,
        })
        self.product.product_tmpl_id.with_context(blend_inbound_sync=True).write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": group.id})],
        })

        response = BlendPOSAPIService(self.env, self.pos_config).get_menu(
            filters={"product_id": str(self.product.product_tmpl_id.id)}
        )
        rows = [row for category in response["categories"] for row in category["products"]]
        parent = next(row for row in rows if row["id"] == self.product.product_tmpl_id.id)
        ids = [
            option["id"] for group_data in parent["modifier_groups"]
            for option in group_data["modifiers"]
        ]
        self.assertEqual(ids, [self._menu_option_id(self.product, available_option)])
        for hidden in (closed_option, archived_product_option):
            self.assertNotIn(self._menu_option_id(self.product, hidden), ids)
        self.assertEqual(len(ids), 1)
        group.with_context(blend_inbound_sync=True).write({"blend_is_available": False})
        closed_response = BlendPOSAPIService(self.env, self.pos_config).get_menu(
            filters={"product_id": str(self.product.product_tmpl_id.id)}
        )
        closed_rows = [
            row for category in closed_response["categories"]
            for row in category["products"]
        ]
        self.assertEqual(closed_rows[0]["modifier_groups"], [])

    def test_menu_includes_hidden_local_modifier_under_parent_only(self):
        self.connection.menu_pricelist_id = self.env["product.pricelist"].create({
            "name": "Blend hidden modifier catalogue",
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
        })
        option_product = self.env["product.product"].create({
            "name": "Hidden local cheese",
            "available_in_pos": False,
            "blend_is_available": False,
            "is_storable": False,
        })
        closed_blend_product = self.env["product.product"].create({
            "name": "Disabled Blend cheese",
            "available_in_pos": False,
            "blend_external_id": "900001",
            "blend_is_available": False,
            "is_storable": False,
        })
        group = self.env["pos.modifier.group"].create({
            "name": "Shared burger extras",
            "company_id": self.company.id,
            "option_ids": [Command.create({
                "name": "Hidden local cheese",
                "option_type": "product",
                "product_id": option_product.id,
                "price_mode": "fixed_extra",
                "extra_price": 2.0,
            }), Command.create({
                "name": "Disabled Blend cheese",
                "option_type": "product",
                "product_id": closed_blend_product.id,
            })],
        })
        self.product.product_tmpl_id.write({
            "blend_external_id": str(self.product.product_tmpl_id.id),
            "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": group.id})],
        })
        service = BlendPOSAPIService(self.env, self.pos_config)
        menu = service.get_menu(filters={"product_id": str(self.product.product_tmpl_id.id)})
        parent = next(row for category in menu["categories"] for row in category["products"])
        modifiers = parent["modifier_groups"][0]["modifiers"]
        self.assertEqual(len(modifiers), 1)
        self.assertEqual(
            modifiers[0]["id"], self._menu_option_id(self.product, group.option_ids[0])
        )
        self.assertTrue(modifiers[0]["status"])
        self.assertEqual(modifiers[0]["price"], 2.0)
        hidden_menu = service.get_menu(filters={"product_id": str(option_product.product_tmpl_id.id)})
        self.assertFalse(any(category["products"] for category in hidden_menu["categories"]))

    def test_menu_requires_connection_pricelist(self):
        with self.assertRaises(BlendAPIError) as caught:
            BlendPOSAPIService(self.env, self.pos_config).get_menu()
        self.assertEqual(caught.exception.status_code, 409)

    def test_menu_modifier_product_uses_connection_pricelist(self):
        products = self.env["product.product"].with_context(blend_inbound_sync=True)
        modifier_product = products.create({
            "name": "Blend Premium Sauce",
            "available_in_pos": True,
            "list_price": 12.0,
        })
        group = self.env["pos.modifier.group"].create({
            "name": "Blend Extras", "selection_type": "multiple",
            "min_qty": 0, "max_qty": 2, "company_id": self.company.id,
        })
        options = self.env["pos.modifier.option"].with_context(blend_inbound_sync=True)
        priced_option = options.create({
            "name": "Premium Sauce", "group_id": group.id,
            "option_type": "product", "product_id": modifier_product.id,
            "price_mode": "product_pricelist",
        })
        fixed_product = products.create({
            "name": "Blend Fixed Extra", "available_in_pos": True, "list_price": 12.0,
        })
        fixed_option = options.create({
            "name": "Fixed Extra", "group_id": group.id,
            "option_type": "product", "product_id": fixed_product.id,
            "price_mode": "fixed_extra", "extra_price": 4.0,
        })
        self.product.product_tmpl_id.with_context(blend_inbound_sync=True).write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": group.id})],
        })
        self.product.with_context(blend_inbound_sync=True).write({
            "blend_external_id": str(self.product.id),
        })
        pricelist = self.env["product.pricelist"].create({
            "name": "Blend Modifier Prices",
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
            "item_ids": [Command.create({
                "applied_on": "0_product_variant", "product_id": modifier_product.id,
                "compute_price": "fixed", "fixed_price": 7.0,
            })],
        })
        self.connection.menu_pricelist_id = pricelist
        result = BlendPOSAPIService(self.env, self.pos_config).get_menu(
            filters={"product_id": str(self.product.id)}
        )
        row = next(product for category in result["categories"] for product in category["products"])
        options = {
            option["id"]: option["price"]
            for modifier_group in row["modifier_groups"]
            for option in modifier_group["modifiers"]
        }
        self.assertEqual(options[self._menu_option_id(self.product, priced_option)], 7.0)
        self.assertEqual(options[self._menu_option_id(self.product, fixed_option)], 4.0)

