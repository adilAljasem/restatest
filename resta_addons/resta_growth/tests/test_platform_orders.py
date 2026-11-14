from odoo.tests import tagged

from .common import GrowthCase


@tagged("post_install", "-at_install")
class TestPlatformOrders(GrowthCase):
    """Orders sent by the Resta platform (resta_saas_tenant) to the growth journey."""

    def _order(self, remote_id, kind, payload):
        Command = self.env["resta.platform.command"].sudo()
        Command._receive([{"id": remote_id, "kind": kind, "payload": payload}])
        command = Command.search([("remote_id", "=", remote_id)])
        result = command.with_context(resta_platform_command=True)._run()
        return command, result

    def test_unlock_feature(self):
        self.assertFalse(self.inventory.unlocked)
        _cmd, result = self._order(901, "unlock_features", {"keys": ["inventory"]})
        self.assertTrue(self.inventory.unlocked)
        self.assertIn(self.inventory.name, result)
        self.assertFalse(self.accounting.unlocked)

    def test_reach_level(self):
        self.profile.sudo()._refresh_level_state()
        self._order(902, "achieve_level", {"level": 1})
        self.assertTrue(self.inventory.unlocked, "level 1 reward: inventory")
        self.assertEqual(self.profile.current_level_id, self.level2)

    def test_paid_in_full_opens_everything(self):
        self._order(903, "set_growth_plan", {"growth_plan": "paid_full"})
        self.assertEqual(self.profile.plan, "paid_full")
        self.assertTrue(self.inventory.unlocked and self.accounting.unlocked)

    def test_plan_from_entitlements(self):
        self.env["resta.platform.command"].sudo()._apply_entitlements({"growth_plan": "paid_full", "max_users": 5})
        self.assertEqual(self.profile.plan, "paid_full")
        self.assertTrue(self.accounting.unlocked)

    def test_owner_settings_cannot_install_apps(self):
        """The owner saves the settings as superuser (growth); app switches are ignored."""
        settings = self.env["res.config.settings"].create({})
        classified = settings._get_classified_fields()["module"]
        target = classified.filtered(lambda m: m.state == "uninstalled")[:1]
        if not target:
            self.skipTest("no uninstalled module behind a settings switch")
        settings["module_%s" % target.name] = True
        settings._resta_keep_modules_as_installed()
        self.assertFalse(settings["module_%s" % target.name])
