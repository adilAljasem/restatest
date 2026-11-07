import base64

from odoo.modules.module import get_module_icon
from odoo.tests import TransactionCase, tagged
from odoo.tools import file_open
from odoo.tools.image import image_process


@tagged("post_install", "-at_install")
class TestBlendBranding(TransactionCase):
    def test_module_and_registered_provider_use_supplied_logo(self):
        icon_path = "/bs_pos_delivery_blend/static/description/icon.png"
        self.assertEqual(get_module_icon("bs_pos_delivery_blend"), icon_path)
        with file_open(icon_path.lstrip("/"), "rb") as icon_file:
            icon = icon_file.read()
        with file_open("bs_pos_delivery_blend/static/img/img.png", "rb") as source_file:
            self.assertEqual(icon, source_file.read())

        provider = self.env.ref("bs_pos_delivery_core.provider_blend")
        self.assertEqual(provider.module_id.icon, icon_path)
        self.assertEqual(provider.module_id.icon_image, base64.b64encode(icon))
        self.assertEqual(
            provider.image_128,
            base64.b64encode(image_process(icon, size=(128, 128))),
        )
        self.assertTrue(provider._pos_logo_data_url().startswith("data:image/png;base64,"))
