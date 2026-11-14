from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRestaSettings(TransactionCase):

    def test_home_data(self):
        data = self.env["resta.settings.home"].get_home_data()
        self.assertTrue(data["sections"])
        self.assertIn("company", [i["key"] for i in data["checklist"]])
        self.assertEqual(data["total"], len(data["checklist"]))
        self.assertTrue(0 <= data["progress"] <= 100)
        cards = [c for s in data["sections"] for c in s["cards"]]
        self.assertIn("company", [c["key"] for c in cards])
        for card in cards:
            for link in card["links"]:
                self.assertTrue(link["action"], "links to missing actions are dropped")

    def test_settings_page_is_simplified(self):
        arch, _view = self.env["res.config.settings"]._get_view(view_type="form")
        apps = [a.get("name") for a in arch.iter("app")]
        self.assertEqual(apps[0], "general_settings")
        blocks = {(b.get("id") or b.get("name")): b for b in arch.iter("block")}
        if "integration" in blocks:
            self.assertEqual(blocks["integration"].get("groups"), "base.group_no_one")
        installed = set(self.env["ir.module.module"].search([]).mapped("name"))
        for setting in arch.iter("setting"):
            for field in setting.iter("field"):
                name = field.get("name") or ""
                if field.get("widget") == "upgrade_boolean" and name.startswith("module_") \
                        and name[7:] not in installed:
                    self.assertEqual(setting.get("invisible"), "1", "%s is an upsell" % name)
        if "calendar" in apps:
            self.assertEqual(apps[-1] if "resta_growth" not in apps else apps[-2], "calendar")

    def test_settings_view_loads(self):
        views = self.env["res.config.settings"].get_views([(False, "form")])
        self.assertTrue(views["views"]["form"]["arch"])
