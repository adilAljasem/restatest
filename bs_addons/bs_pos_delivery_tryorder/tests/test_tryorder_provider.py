from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTryOrderProvider(TransactionCase):
    def test_tryorder_is_registered_as_a_core_provider(self):
        provider = self.env["bs.delivery.provider"].search([("code", "=", "tryorder")])
        self.assertEqual(len(provider), 1)
        self.assertEqual(provider.module_id.name, "bs_pos_delivery_tryorder")
        self.assertEqual(provider.adapter_model_name, "bs.delivery.adapter.tryorder")
