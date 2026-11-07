from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDeliveryApiUserSettings(TransactionCase):
    def test_default_api_user_can_be_replaced_in_settings(self):
        default_user = self.env.ref(
            "bs_pos_delivery_core.user_delivery_api", raise_if_not_found=False
        )
        self.assertTrue(default_user)
        self.assertTrue(default_user.active)

        Settings = self.env["res.config.settings"]
        settings = Settings.create({})
        self.assertEqual(settings.bs_delivery_api_user_id, default_user)

        replacement = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Replacement Delivery API User",
            "login": "replacement_delivery_api_user",
        })
        settings.bs_delivery_api_user_id = replacement
        settings.execute()

        self.assertEqual(Settings.create({}).bs_delivery_api_user_id, replacement)
