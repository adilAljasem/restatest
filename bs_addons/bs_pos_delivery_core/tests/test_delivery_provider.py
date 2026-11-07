import base64
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools import file_open


@tagged("post_install", "-at_install")
class TestDeliveryProvider(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Provider = cls.env["bs.delivery.provider"]
        cls.manager = new_test_user(
            cls.env,
            login="delivery_manager",
            groups="bs_pos_delivery_core.group_delivery_integration_manager",
        )

    def test_connector_registers_provider_without_core_dependency_on_its_name(self):
        module = self.env["ir.module.module"].create(
            {
                "name": "bs_pos_delivery_registered_test",
                "shortdesc": "Registered Test Delivery",
                "state": "uninstalled",
            }
        )

        provider = self.Provider.register_provider(
            "registered_test",
            {
                "name": "Registered Provider",
                "adapter_model_name": "bs.delivery.adapter.registered_test",
            },
            module.name,
        )

        self.assertEqual(provider.module_id, module)
        self.assertEqual(provider.adapter_model_name, "bs.delivery.adapter.registered_test")
        self.assertEqual(
            self.env.ref("bs_pos_delivery_core.provider_registered_test"),
            provider,
        )

    def test_provider_code_is_registry_safe(self):
        with self.assertRaises(ValidationError):
            self.Provider.create({"name": "Invalid", "code": "Invalid-Code"})

    def test_pos_logo_data_url_preserves_svg_media_type(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
        provider = self.Provider.create(
            {
                "name": "SVG Logo Provider",
                "code": "svg_logo_provider",
                "image_128": base64.b64encode(svg),
            }
        )

        self.assertTrue(
            provider._pos_logo_data_url().startswith("data:image/svg+xml;base64,")
        )

    def test_non_system_manager_cannot_install_modules(self):
        module = self.env["ir.module.module"].create(
            {
                "name": "bs_pos_delivery_access_test",
                "shortdesc": "Access Test Delivery",
                "state": "uninstalled",
            }
        )
        provider = self.Provider.create(
            {
                "name": "Access Test",
                "code": "access_test",
                "module_id": module.id,
            }
        )
        with self.assertRaises(AccessError):
            provider.with_user(self.manager).action_install_module()

    def test_configure_opens_new_connection_form(self):
        provider = self.Provider.create({"name": "Unmanaged Test", "code": "unmanaged_test"})
        action = provider.action_configure()

        self.assertEqual(action["res_model"], "bs.delivery.connection")
        self.assertEqual(action["view_mode"], "form")
        self.assertEqual(action["context"]["default_provider_id"], provider.id)
        self.assertEqual(action["context"]["default_company_id"], self.env.company.id)

    def test_provider_module_discovery_uses_technical_name_convention(self):
        module = self.env["ir.module.module"].create(
            {
                "name": "bs_pos_delivery_future_test",
                "shortdesc": "Future Test Delivery",
                "summary": "Discovered only for this test.",
                "state": "uninstalled",
            }
        )

        with patch.object(type(self.Provider), "_module_is_available", return_value=True):
            self.Provider._sync_available_provider_modules()

        provider = self.Provider.search([("code", "=", "future_test")])
        self.assertEqual(provider.module_id, module)
        self.assertEqual(provider.name, "Future Test Delivery")

    def test_catalog_excludes_missing_module_without_removing_history(self):
        module = self.env["ir.module.module"].create(
            {"name": "bs_pos_delivery_retired_fixture", "state": "uninstalled"}
        )
        provider = self.Provider.create(
            {"name": "Retired Fixture", "code": "retired_fixture", "module_id": module.id}
        )
        unmanaged = self.Provider.create({"name": "Manual Fixture", "code": "manual_fixture"})

        action = self.Provider.action_open_provider_catalog()
        visible = self.Provider.search(action["domain"])

        self.assertNotIn(provider, visible)
        self.assertIn(unmanaged, visible)
        self.assertIn(provider, self.Provider.search([("module_available", "=", False)]))
        self.assertTrue(provider.exists())
        self.assertTrue(provider.active)
        self.assertFalse(provider.module_available)

    def test_discovery_does_not_recreate_or_reactivate_missing_modules(self):
        modules = self.env["ir.module.module"].create([
            {"name": "bs_pos_delivery_missing_fixture", "state": "uninstalled"},
            {"name": "bs_pos_delivery_archived_fixture", "state": "uninstalled"},
        ])
        archived = self.Provider.create({
            "name": "Archived Fixture", "code": "archived_fixture",
            "module_id": modules[1].id, "active": False,
        })

        self.Provider._sync_available_provider_modules()

        self.assertFalse(self.Provider.with_context(active_test=False).search([
            ("code", "=", "missing_fixture"),
        ]))
        self.assertFalse(archived.active)

    def test_old_missing_module_card_rejects_install_and_configuration(self):
        module = self.env["ir.module.module"].create(
            {"name": "bs_pos_delivery_missing_card", "state": "installed"}
        )
        provider = self.Provider.create(
            {"name": "Missing Card", "code": "missing_card", "module_id": module.id}
        )
        self.assertFalse(provider.is_operational)
        with self.assertRaises(UserError):
            provider.action_install_module()
        with self.assertRaises(UserError):
            provider.action_configure()

    def test_provider_catalog_menu_runs_discovery_server_action(self):
        menu = self.env.ref("bs_pos_delivery_core.menu_bs_delivery_integrations")
        server_action = self.env.ref(
            "bs_pos_delivery_core.action_open_bs_delivery_provider_catalog"
        )

        self.assertEqual(menu.action, server_action)

    def test_catalog_refreshes_placeholder_without_installing_connector(self):
        module = self.env["ir.module.module"].create(
            {
                "name": "bs_pos_delivery_icon_test",
                "shortdesc": "Icon Test",
                "state": "uninstalled",
                "icon": "/bs_pos_delivery_core/static/description/icon.svg",
            }
        )
        with file_open("base/static/description/icon.png", "rb") as icon_file:
            default_icon = base64.b64encode(icon_file.read())
        provider = self.Provider.create(
            {
                "name": "Icon Test",
                "code": "icon_test",
                "module_id": module.id,
                "image_128": default_icon,
            }
        )
        self.assertNotEqual(provider.image_128, module.icon_image)

        with patch.object(type(self.Provider), "_module_is_available", return_value=True):
            self.Provider._sync_available_provider_modules()

        self.assertEqual(provider.image_128, module.icon_image)
        self.assertEqual(module.state, "uninstalled")
        self.assertEqual(
            provider._pos_logo_data_url(),
            "data:image/svg+xml;base64," + module.icon_image.decode(),
        )

    def test_catalog_preserves_custom_logo(self):
        module = self.env["ir.module.module"].create(
            {
                "name": "bs_pos_delivery_custom_icon_test",
                "shortdesc": "Custom Icon Test",
                "state": "uninstalled",
                "icon": "/bs_pos_delivery_core/static/description/icon.svg",
            }
        )
        custom_icon = base64.b64encode(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>'
        )
        provider = self.Provider.create(
            {
                "name": "Custom Icon Test",
                "code": "custom_icon_test",
                "module_id": module.id,
                "image_128": custom_icon,
            }
        )

        with patch.object(type(self.Provider), "_module_is_available", return_value=True):
            self.Provider._sync_available_provider_modules()

        self.assertEqual(provider.image_128, custom_icon)

    def test_install_delegates_to_odoo_module_lifecycle(self):
        module = self.env["ir.module.module"].create(
            {
                "name": "bs_pos_delivery_install_test",
                "shortdesc": "Install Test Delivery",
                "state": "uninstalled",
            }
        )
        provider = self.Provider.create(
            {
                "name": "Install Test",
                "code": "install_test",
                "module_id": module.id,
            }
        )

        with (
            patch.object(type(self.Provider), "_module_is_available", return_value=True),
            patch.object(type(module), "button_immediate_install", autospec=True) as install,
        ):
            action = provider.action_install_module()

        install.assert_called_once()
        self.assertEqual(action, {"type": "ir.actions.client", "tag": "reload"})

    def test_uninstall_disables_connections_before_module_removal(self):
        module = self.env["ir.module.module"].create(
            {
                "name": "bs_pos_delivery_uninstall_test",
                "shortdesc": "Uninstall Test Delivery",
                "state": "installed",
            }
        )
        provider = self.Provider.create(
            {
                "name": "Uninstall Test",
                "code": "uninstall_test",
                "module_id": module.id,
            }
        )
        connection = self.env["bs.delivery.connection"].create(
            {
                "name": "Connection to Disable",
                "provider_id": provider.id,
            }
        )

        with patch.object(type(module), "button_immediate_uninstall", autospec=True) as uninstall:
            provider.action_uninstall_module()

        uninstall.assert_called_once()
        self.assertFalse(connection.active)
        self.assertEqual(connection.state, "disabled")
        self.assertIn("uninstalled", connection.last_error)

    def test_uninstall_removes_owned_companion_modules_together(self):
        adapter = self.env["ir.module.module"].create(
            {
                "name": "bs_pos_delivery_owned_test",
                "shortdesc": "Owned Test Adapter",
                "state": "installed",
            }
        )
        companion = self.env["ir.module.module"].create(
            {
                "name": "owned_test_connector",
                "shortdesc": "Owned Test Connector",
                "state": "installed",
            }
        )
        provider = self.Provider.create(
            {
                "name": "Owned Test",
                "code": "owned_test",
                "module_id": adapter.id,
                "companion_module_ids": [(6, 0, companion.ids)],
            }
        )

        with (
            patch.object(
                type(companion),
                "downstream_dependencies",
                autospec=True,
                return_value=self.env["ir.module.module"],
            ),
            patch.object(
                type(adapter),
                "button_immediate_uninstall",
                autospec=True,
            ) as uninstall,
        ):
            provider.action_uninstall_module()

        uninstall.assert_called_once()
        self.assertEqual(uninstall.call_args.args[0], adapter | companion)

    def test_orphan_companion_requires_cleanup(self):
        adapter = self.env["ir.module.module"].create(
            {
                "name": "bs_pos_delivery_cleanup_test",
                "shortdesc": "Cleanup Test Adapter",
                "state": "uninstalled",
            }
        )
        companion = self.env["ir.module.module"].create(
            {
                "name": "cleanup_test_connector",
                "shortdesc": "Cleanup Test Connector",
                "state": "installed",
            }
        )
        provider = self.Provider.create(
            {
                "name": "Cleanup Test",
                "code": "cleanup_test",
                "module_id": adapter.id,
                "companion_module_ids": [(6, 0, companion.ids)],
            }
        )

        self.assertTrue(provider.has_installed_companion_modules)
        self.assertEqual(provider.configuration_state, "cleanup_required")

    def test_shared_companion_blocks_destructive_cleanup(self):
        adapter = self.env["ir.module.module"].create(
            {
                "name": "bs_pos_delivery_shared_test",
                "shortdesc": "Shared Test Adapter",
                "state": "installed",
            }
        )
        companion = self.env["ir.module.module"].create(
            {
                "name": "shared_test_connector",
                "shortdesc": "Shared Test Connector",
                "state": "installed",
            }
        )
        consumer = self.env["ir.module.module"].create(
            {
                "name": "shared_test_consumer",
                "shortdesc": "Shared Test Consumer",
                "state": "installed",
            }
        )
        provider = self.Provider.create(
            {
                "name": "Shared Test",
                "code": "shared_test",
                "module_id": adapter.id,
                "companion_module_ids": [(6, 0, companion.ids)],
            }
        )

        with patch.object(
            type(companion),
            "downstream_dependencies",
            autospec=True,
            return_value=consumer,
        ):
            with self.assertRaises(UserError):
                provider.action_uninstall_module()
