from odoo.http import request

from odoo.addons.web.controllers.webmanifest import WebManifest

from ..models.brand import BRAND, BRAND_COLOR, IMG


class RestaWebManifest(WebManifest):
    """The app installed on phones and desktops (PWA) is «Resta», with the Resta icon."""

    def _get_webmanifest(self):
        manifest = super()._get_webmanifest()
        name = request.env["ir.config_parameter"].sudo().get_param("web.web_app_name") or BRAND
        manifest.update({
            "name": BRAND if name == "Odoo" else name,
            "background_color": "#FFFFFF",
            "theme_color": BRAND_COLOR,
            "icons": [{"src": IMG + "resta-icon-%s.png" % size, "sizes": "%sx%s" % (size, size), "type": "image/png"}
                      for size in (192, 512)],
        })
        return manifest
