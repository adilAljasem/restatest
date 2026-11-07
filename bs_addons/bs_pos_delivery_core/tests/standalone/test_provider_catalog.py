"""Database-free checks of the real provider methods and local addon manifests.

Run with the workspace's native Python; no Odoo registry/database is opened.
ORM boundaries are replaced only where the catalog would query stored records.
"""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


WORKSPACE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(WORKSPACE / "odoo"))

import odoo  # noqa: E402
from odoo.exceptions import UserError  # noqa: E402
from odoo.modules.module import initialize_sys_path  # noqa: E402

odoo.tools.config["addons_path"] = ",".join(
    str(WORKSPACE / folder) for folder in ("aggregator_connect", "odoo/addons")
)
initialize_sys_path()
MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "delivery_provider.py"
spec = importlib.util.spec_from_file_location(
    "odoo.addons.bs_pos_delivery_core.models.delivery_provider", MODEL_PATH
)
provider_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider_module)
Provider = provider_module.DeliveryProvider


class ModuleRows(list):
    def _module_is_available(self, name):
        return Provider._module_is_available(name)

    @property
    def ids(self):
        return [item.id for item in self]

    def filtered(self, predicate):
        return ModuleRows(item for item in self if predicate(item))


class CatalogLookup(Exception):
    """Stop before any catalog write, after capturing its real lookup domain."""


class TestProviderCatalog(unittest.TestCase):
    def test_availability_uses_manifests_and_preserves_unmanaged_providers(self):
        providers = ModuleRows([
            SimpleNamespace(id=21, module_id=SimpleNamespace(name="bs_pos_delivery_feedus")),
            SimpleNamespace(id=22, module_id=SimpleNamespace(name="bs_pos_delivery_retired_fixture")),
            SimpleNamespace(id=23, module_id=False),
        ])
        Provider._compute_module_available(providers)
        self.assertEqual([item.module_available for item in providers], [True, False, True])

    def test_non_installable_manifest_is_not_available(self):
        with patch.object(provider_module.Manifest, "for_addon", return_value={"installable": False}):
            self.assertFalse(Provider._module_is_available("bs_pos_delivery_disabled_fixture"))

    def test_catalog_search_excludes_old_records_without_writing_them(self):
        providers = ModuleRows([
            SimpleNamespace(id=21, module_available=True),
            SimpleNamespace(id=22, module_available=False),
        ])
        model = Mock()
        model.sudo.return_value.with_context.return_value.search.return_value = providers
        self.assertEqual(
            Provider._search_module_available(model, "in", [True]),
            [("id", "not in", [22])],
        )
        self.assertEqual(
            Provider._search_module_available(model, "in", [False]),
            [("id", "in", [22])],
        )
        self.assertEqual(Provider._search_module_available(model, "in", []), [("id", "in", [])])
        self.assertEqual(Provider._search_module_available(model, "in", [True, False]), [])
        self.assertIs(Provider._search_module_available(model, "not in", [True]), NotImplemented)
        model.sudo.return_value.with_context.assert_called_with(active_test=False)

    def test_discovery_ignores_database_module_without_files(self):
        modules = ModuleRows([
            SimpleNamespace(id=11, name="bs_pos_delivery_feedus"),
            SimpleNamespace(id=12, name="bs_pos_delivery_retired_fixture"),
        ])
        module_model = Mock()
        module_model.sudo.return_value.search.return_value = modules
        catalog = Mock()
        catalog.with_context.return_value.search.side_effect = CatalogLookup
        model = SimpleNamespace(
            env={"ir.module.module": module_model},
            _MODULE_PREFIX=Provider._MODULE_PREFIX,
            _CORE_MODULE=Provider._CORE_MODULE,
            _NON_PROVIDER_MODULES=Provider._NON_PROVIDER_MODULES,
            sudo=lambda: catalog,
            _module_is_available=lambda name: Provider._module_is_available(name),
        )
        model._codes_from_modules = lambda rows: Provider._codes_from_modules(model, rows)

        with self.assertRaises(CatalogLookup):
            Provider._sync_available_provider_modules(model)

        domain = catalog.with_context.return_value.search.call_args.args[0]
        self.assertIn(("module_id", "in", [11]), domain)
        self.assertIn(("code", "in", ["feedus"]), domain)

    def test_missing_module_cannot_be_installed_through_old_card(self):
        install = Mock()
        provider = SimpleNamespace(
            ensure_one=lambda: None,
            _check_module_management_access=lambda: None,
            module_id=SimpleNamespace(button_immediate_install=install),
            module_state="uninstalled",
            module_available=False,
        )
        with patch.object(provider_module, "_", side_effect=lambda message: message):
            with self.assertRaises(UserError):
                Provider.action_install_module(provider)
        install.assert_not_called()

    def test_missing_module_cannot_be_configured_through_old_card(self):
        provider = SimpleNamespace(ensure_one=lambda: None, module_available=False)
        with patch.object(provider_module, "_", side_effect=lambda message: message):
            with self.assertRaises(UserError):
                Provider.action_configure(provider)

    def test_existing_uninstalled_connector_can_still_be_installed(self):
        install = Mock()
        provider = SimpleNamespace(
            ensure_one=lambda: None,
            _check_module_management_access=lambda: None,
            module_id=SimpleNamespace(button_immediate_install=install),
            module_state="uninstalled",
            module_available=True,
        )
        self.assertEqual(
            Provider.action_install_module(provider),
            {"type": "ir.actions.client", "tag": "reload"},
        )
        install.assert_called_once_with()

    def test_catalog_action_filters_even_before_view_data_is_upgraded(self):
        action = {"res_model": "bs.delivery.provider", "domain": []}
        env = Mock()
        env.ref.return_value.read.return_value = [action]
        provider = SimpleNamespace(env=env, _sync_available_provider_modules=lambda: None)
        self.assertEqual(
            Provider.action_open_provider_catalog(provider)["domain"],
            [("module_available", "=", True)],
        )

    def test_missing_module_is_not_operational_even_with_stale_installed_state(self):
        provider = SimpleNamespace(
            active=True,
            module_id=SimpleNamespace(name="bs_pos_delivery_retired_fixture"),
            module_state="installed",
            module_available=False,
            implementation_state="ready",
        )
        Provider._compute_is_operational([provider])
        self.assertFalse(provider.is_operational)

    def test_existing_provider_operational_states_are_preserved(self):
        for module, state, expected in (
            (False, False, True),
            (SimpleNamespace(name="bs_pos_delivery_feedus"), "installed", True),
            (SimpleNamespace(name="bs_pos_delivery_feedus"), "uninstalled", False),
        ):
            with self.subTest(state=state):
                provider = SimpleNamespace(
                    active=True, module_id=module, module_state=state,
                    module_available=True, implementation_state="ready",
                )
                Provider._compute_is_operational([provider])
                self.assertEqual(provider.is_operational, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
