from odoo import models
from odoo.http import request
from odoo.tools.misc import file_path

COOKIE = "color_scheme"
STATIC = "/resta_sidebar_theme/static/img/"


def _custom_static_logo():
    """Return the URL of a brand logo dropped into static/img, if any."""
    for name in ("resta-wordmark.svg", "resta-wordmark.png"):
        try:
            file_path("resta_sidebar_theme/static/img/" + name)
            return STATIC + name
        except FileNotFoundError:
            continue
    return False


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def color_scheme(self):
        """Serve Odoo's dark asset bundle when the user chose dark mode.

        The choice lives in the `color_scheme` cookie (same cookie Odoo uses).
        Without a cookie, the company default applies ("system" is resolved
        in the browser on first load).
        """
        scheme = super().color_scheme()
        if not request:
            return scheme
        if "color_scheme" in self.env["res.users.settings"]._fields:
            # Odoo Enterprise manages dark mode itself (per-user setting).
            return scheme
        cookie = request.httprequest.cookies.get(COOKIE)
        if cookie in ("light", "dark"):
            return cookie
        if request.session.uid:
            company = self.env.company
            if company.rs_default_scheme == "dark":
                return "dark"
            if company.rs_default_scheme == "light":
                return "light"
        return scheme

    def session_info(self):
        info = super().session_info()
        if not request or not request.session.uid:
            return info
        company = self.env.company.sudo()

        def img(field):
            if not company[field]:
                return False
            unique = int(company.write_date.timestamp()) if company.write_date else 0
            return f"/web/image/res.company/{company.id}/{field}?unique={unique}"

        static_logo = _custom_static_logo()
        info["resta_theme"] = {
            "scheme": self.color_scheme(),
            "default_scheme": company.rs_default_scheme,
            "sidebar_default": company.rs_sidebar_default,
            "group_sections": company.rs_group_sections,
            "icon_style": company.rs_icon_style,
            "logo_light": img("rs_logo_light") or static_logo
            or STATIC + "resta-wordmark-default.svg",
            "logo_dark": img("rs_logo_dark"),
            "logo_dark_fallback": False if (img("rs_logo_light") or static_logo)
            else STATIC + "resta-wordmark-default-dark.svg",
            "logo_dark_treatment": company.rs_logo_dark_treatment,
            "logo_mark": img("rs_logo_mark") or STATIC + "resta-mark.svg",
        }
        return info
