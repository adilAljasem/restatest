from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestDeliveryConnection(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Provider = cls.env["bs.delivery.provider"]
        cls.Connection = cls.env["bs.delivery.connection"]
        cls.provider = cls.Provider.create(
            {
                "name": "Test Provider",
                "code": "test_provider",
            }
        )

    def test_missing_adapter_is_reported_without_losing_diagnostics(self):
        connection = self.Connection.create(
            {
                "name": "Missing Adapter",
                "provider_id": self.provider.id,
            }
        )

        action = connection.action_test_connection()

        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "danger")
        self.assertEqual(connection.state, "error")
        self.assertTrue(connection.last_test_date)
        self.assertIn("adapter is not available", connection.last_error)

    def test_provider_configuration_state_uses_current_company_connections(self):
        self.assertEqual(self.provider.configuration_state, "not_configured")
        connection = self.Connection.create(
            {
                "name": "Main Account",
                "provider_id": self.provider.id,
                "state": "draft",
            }
        )
        self.provider.invalidate_recordset()
        self.assertEqual(self.provider.configuration_state, "configured")

        connection.state = "connected"
        self.provider.invalidate_recordset()
        self.assertEqual(self.provider.configuration_state, "connected")
        self.assertEqual(self.provider.connected_connection_count, 1)

    def test_company_record_rule_isolates_connections(self):
        other_company = self.env["res.company"].create({"name": "Other Delivery Company"})
        visible_connection = self.Connection.create(
            {
                "name": "Visible Connection",
                "provider_id": self.provider.id,
                "company_id": self.env.company.id,
            }
        )
        hidden_connection = self.Connection.with_company(other_company).create(
            {
                "name": "Hidden Connection",
                "provider_id": self.provider.id,
                "company_id": other_company.id,
            }
        )
        manager = new_test_user(
            self.env,
            login="single_company_delivery_manager",
            groups="bs_pos_delivery_core.group_delivery_integration_manager",
            company_id=self.env.company.id,
        )

        visible = self.Connection.with_user(manager).search(
            [("id", "in", [visible_connection.id, hidden_connection.id])]
        )

        self.assertEqual(visible.mapped("name"), ["Visible Connection"])

    def test_menu_pricelist_uses_connection_currency(self):
        pricelist = self.env["product.pricelist"].create({
            "name": "Connection Menu Prices",
            "company_id": self.env.company.id,
            "currency_id": self.env.company.currency_id.id,
        })
        connection = self.Connection.create({
            "name": "Priced Connection",
            "provider_id": self.provider.id,
            "menu_pricelist_id": pricelist.id,
        })
        self.assertEqual(connection.menu_pricelist_id, pricelist)

        other_currency = self.env["res.currency"].search([
            ("id", "!=", self.env.company.currency_id.id),
        ], limit=1)
        other_pricelist = self.env["product.pricelist"].create({
            "name": "Wrong Currency Menu Prices",
            "currency_id": other_currency.id,
        })
        with self.assertRaises(ValidationError):
            connection.menu_pricelist_id = other_pricelist

    def test_menu_pricelist_is_checked_again_when_publishing(self):
        pricelist = self.env["product.pricelist"].create({
            "name": "Published Menu Prices",
            "currency_id": self.env.company.currency_id.id,
        })
        connection = self.Connection.create({
            "name": "Published Menu Connection",
            "provider_id": self.provider.id,
            "menu_pricelist_id": pricelist.id,
        })
        self.assertEqual(connection._get_menu_pricelist(), pricelist)
        pricelist.active = False
        self.assertFalse(connection._get_menu_pricelist())
        pricelist.active = True
        other_currency = self.env["res.currency"].search([
            ("id", "!=", self.env.company.currency_id.id),
        ], limit=1)
        pricelist.currency_id = other_currency
        self.assertFalse(connection._get_menu_pricelist())

    def test_reset_to_draft_clears_error(self):
        connection = self.Connection.create(
            {
                "name": "Connection in Error",
                "provider_id": self.provider.id,
                "state": "error",
                "last_error": "Temporary test error",
                "last_test_date": fields.Datetime.now(),
            }
        )

        connection.action_reset_to_draft()

        self.assertEqual(connection.state, "draft")
        self.assertFalse(connection.last_error)

    def test_connection_is_allowed_during_provider_installation(self):
        module = self.env["ir.module.module"].create(
            {
                "name": "bs_pos_delivery_installing_connection_test",
                "shortdesc": "Installing Connection Test",
                "state": "to install",
            }
        )
        provider = self.Provider.create(
            {
                "name": "Installing Provider",
                "code": "installing_provider",
                "module_id": module.id,
            }
        )

        connection = self.Connection.create(
            {
                "name": "Created by Post Install Hook",
                "provider_id": provider.id,
            }
        )

        self.assertTrue(connection.active)

    def test_active_connection_is_blocked_for_uninstalled_provider(self):
        module = self.env["ir.module.module"].create(
            {
                "name": "bs_pos_delivery_uninstalled_connection_test",
                "shortdesc": "Uninstalled Connection Test",
                "state": "uninstalled",
            }
        )
        provider = self.Provider.create(
            {
                "name": "Uninstalled Provider",
                "code": "uninstalled_provider",
                "module_id": module.id,
            }
        )

        with self.assertRaises(ValidationError):
            self.Connection.create(
                {
                    "name": "Must Be Rejected",
                    "provider_id": provider.id,
                }
            )

    def test_active_production_connection_requires_ready_provider(self):
        self.provider.implementation_state = "sandbox"

        with self.assertRaisesRegex(ValidationError, "production-ready"):
            self.Connection.create({
                "name": "Sandbox Production",
                "provider_id": self.provider.id,
                "environment": "production",
            })

    def test_store_mapping_is_authoritative_for_pos_scope_and_board_union(self):
        config = self.env["pos.config"].create(
            {"name": "Store Scope POS", "module_pos_restaurant": False}
        )
        connection = self.Connection.create(
            {
                "name": "Store Scope Connection",
                "provider_id": self.provider.id,
                "state": "connected",
            }
        )
        self.env["bs.delivery.store.binding"].create(
            {
                "connection_id": connection.id,
                "pos_config_id": config.id,
                "remote_store_id": "STORE-SCOPE-1",
            }
        )
        self.assertIn(config, connection.pos_config_ids)

        connection.write({"pos_config_ids": [Command.unlink(config.id)]})
        self.assertIn(config, connection.pos_config_ids)

        self.env.cr.execute(
            "DELETE FROM bs_delivery_connection_pos_config_rel "
            "WHERE connection_id = %s AND pos_config_id = %s",
            [connection.id, config.id],
        )
        connection.invalidate_recordset(["pos_config_ids"])
        self.assertNotIn(config, connection.pos_config_ids)
        self.assertIn(connection, self.Connection._for_pos_board(config))

    def test_disabling_store_removes_its_pos_scope(self):
        config = self.env["pos.config"].create({"name": "Disabled Store POS"})
        connection = self.Connection.create({
            "name": "Disabled Store Connection",
            "provider_id": self.provider.id,
            "state": "connected",
        })
        store = self.env["bs.delivery.store.binding"].create({
            "connection_id": connection.id,
            "pos_config_id": config.id,
            "remote_store_id": "DISABLED-STORE",
        })
        self.assertIn(config, connection.pos_config_ids)

        store.active = False

        self.assertNotIn(config, connection.pos_config_ids)
        self.assertNotIn(connection, self.Connection._for_pos_board(config))

        store.active = True
        self.assertIn(config, connection.pos_config_ids)
        store.unlink()
        self.assertNotIn(config, connection.pos_config_ids)

    def test_store_mapping_rejects_extra_direct_pos_scope(self):
        mapped = self.env["pos.config"].create({"name": "Mapped Store POS"})
        extra = self.env["pos.config"].create({"name": "Unmapped Store POS"})
        connection = self.Connection.create({
            "name": "Mapped Store Connection",
            "provider_id": self.provider.id,
            "state": "connected",
        })
        self.env["bs.delivery.store.binding"].create({
            "connection_id": connection.id,
            "pos_config_id": mapped.id,
            "remote_store_id": "MAPPED-STORE",
        })
        connection.write({"pos_config_ids": [Command.link(extra.id)]})
        self.assertNotIn(extra, connection.pos_config_ids)
        self.assertNotIn(connection, self.Connection._for_pos_board(extra))

    def test_delivery_manager_can_only_operate_the_pause_switch(self):
        connection = self.Connection.create(
            {
                "name": "Rush Hour Switch",
                "provider_id": self.provider.id,
                "state": "connected",
            }
        )
        manager = new_test_user(
            self.env,
            login="delivery_pause_manager",
            groups="bs_pos_delivery_core.group_delivery_manager",
            company_id=self.env.company.id,
        )
        managed_connection = connection.with_user(manager)

        with self.assertRaises(AccessError):
            managed_connection.write({"paused": True})

        managed_connection.pause_receiving(reason="Rush hour")
        connection.invalidate_recordset(["paused", "pause_reason"])
        self.assertTrue(connection.paused)
        self.assertEqual(connection.pause_reason, "Rush hour")

        managed_connection.resume_receiving()
        connection.invalidate_recordset(
            ["paused", "paused_until", "pause_reason"]
        )
        self.assertFalse(connection.paused)
        self.assertFalse(connection.paused_until)
        self.assertFalse(connection.pause_reason)

        viewer = new_test_user(
            self.env,
            login="delivery_pause_viewer",
            groups="bs_pos_delivery_core.group_delivery_viewer",
            company_id=self.env.company.id,
        )
        with self.assertRaises(AccessError):
            connection.with_user(viewer).pause_receiving()

    def test_connection_shortcuts_count_canonical_operational_records(self):
        connection = self.Connection.create(
            {
                "name": "Operational Shortcuts",
                "provider_id": self.provider.id,
            }
        )
        self.env["bs.delivery.order"]._with_delivery_internal_update().create(
            {
                "connection_id": connection.id,
                "external_order_id": "SHORTCUT-ORDER-1",
                "currency_id": self.env.company.currency_id.id,
                "amount_total": 0.0,
            }
        )
        product = self.env["product.product"].create(
            {"name": "Mapped Shortcut Product"}
        )
        self.env["bs.delivery.product.binding"].create(
            {
                "connection_id": connection.id,
                "product_id": product.id,
                "external_product_id": "SHORTCUT-SKU-1",
            }
        )

        connection.invalidate_recordset(
            ["delivery_order_count", "product_mapping_count"]
        )
        self.assertEqual(connection.delivery_order_count, 1)
        self.assertEqual(connection.product_mapping_count, 1)
        self.assertEqual(
            connection.action_view_delivery_orders()["domain"],
            [("connection_id", "=", connection.id)],
        )
        self.assertEqual(
            connection.action_view_product_mappings()["domain"],
            [("connection_id", "=", connection.id)],
        )

    def test_menu_direction_never_falls_back_to_legacy_sync_menu(self):
        self.provider.capabilities = {"menu_pull": True, "menu_push": True}
        connection = self.Connection.create(
            {
                "name": "Explicit Menu Contract",
                "provider_id": self.provider.id,
            }
        )
        push_version = self.env["bs.delivery.menu.version"].create(
            {
                "name": "Push must stay push",
                "connection_id": connection.id,
                "direction": "push",
            }
        )

        class LegacyPullAdapter:
            @staticmethod
            def supports(operation):
                return operation == "sync_menu"

        with (
            patch.object(
                type(connection),
                "_get_adapter",
                autospec=True,
                return_value=LegacyPullAdapter(),
            ),
            self.assertRaises(ValidationError),
        ):
            push_version._resolve_operation()

    def test_dashboard_rejects_an_explicit_mixed_currency_company_scope(self):
        dashboard = self.env["bs.delivery.dashboard"]
        self.assertEqual(dashboard._allowed_companies(None), self.env.company)
        other_currency = (
            self.env["res.currency"]
            .with_context(active_test=False)
            .search([("id", "!=", self.env.company.currency_id.id)], limit=1)
        )
        other_company = self.env["res.company"].create(
            {
                "name": "Mixed Currency Dashboard Company",
                "currency_id": other_currency.id,
            }
        )
        self.env.user.write({"company_ids": [Command.link(other_company.id)]})
        scoped_dashboard = dashboard.with_context(
            allowed_company_ids=[self.env.company.id, other_company.id]
        )

        with self.assertRaises(ValidationError):
            scoped_dashboard.get_dashboard_data(
                {"company_ids": [self.env.company.id, other_company.id]}
            )
