from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestKdsOrderTimerAssets(TransactionCase):
    def test_backend_bundle_compiles_with_the_patch(self):
        bundle = self.env["ir.qweb"]._get_asset_bundle("web.assets_backend")
        self.assertTrue(bundle.js())
        self.assertTrue(any("kds_order_timer" in f["url"] for f in bundle.files))
