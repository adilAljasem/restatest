"""Replace what Odoo shows of its own brand by Resta, in data that templates cannot reach:
email templates stored in the database, the system bot, the app name, email colours.

Only what users see changes. The copyright and licence notices in Odoo's source files
(LGPL-3) stay as they are.
"""
import base64
import json
import re

from odoo import api, models
from odoo.tools.misc import file_open

BRAND = "Resta"
BRAND_URL = "https://resta.sa"
BRAND_COLOR = "#FF5A1F"
INK_COLOR = "#161616"
IMG = "/resta_sidebar_theme/static/img/"

_ODOO_URL = re.compile(r"https?://(?:www\.)?odoo\.com(?:/[^\"'\s<>\\]*)?(?:\?[^\"'\s<>\\]*)?", re.I)
_ODOO_LOGO_WHITE = re.compile(r"/web/static/img/logo_inverse_white[^\"'\\]*\.png")
_ODOO_LOGO = re.compile(r"/web/static/img/(?:logo|odoo_logo[^\"'\\]*|odoo-icon[^\"'\\]*)\.png")
_ODOO_LINK_TEXT = re.compile(r">\s*Odoo\s*</a>")


def debrand_html(html):
    """Resta instead of Odoo in a piece of HTML (links, logos, «Powered by Odoo»)."""
    if not html or not isinstance(html, str) or "odoo" not in html.lower():
        return html
    html = _ODOO_LOGO_WHITE.sub(IMG + "resta-logo-white.png", html)
    html = _ODOO_LOGO.sub(IMG + "resta-logo.png", html)
    html = _ODOO_URL.sub(BRAND_URL, html)
    html = _ODOO_LINK_TEXT.sub(">%s</a>" % BRAND, html)
    return (html.replace('alt="Odoo"', 'alt="%s"' % BRAND).replace('title="odoo"', 'title="%s"' % BRAND)
            .replace("Odoo Logo", "%s Logo" % BRAND))


class ResCompany(models.Model):
    _inherit = "res.company"

    @api.model
    def _resta_brand_apply(self):
        """Run at install, at every update and daily (templates of modules installed later)."""
        env = self.env
        icp = env["ir.config_parameter"].sudo()
        if icp.get_param("web.web_app_name") in (False, "", "Odoo"):
            icp.set_param("web.web_app_name", BRAND)
        bot = env.ref("base.partner_root", raise_if_not_found=False)
        if bot and bot.name != BRAND:
            with file_open("resta_sidebar_theme/static/img/resta-avatar.png", "rb") as f:
                bot.sudo().write({"name": BRAND, "image_1920": base64.b64encode(f.read())})
        odoo_defaults = {False, "#875A7B", "#875a7b", "#000000", "#714B67"}
        for company in self.sudo().search([]):
            vals = {}
            if "email_primary_color" in company._fields and company.email_primary_color in odoo_defaults:
                vals["email_primary_color"] = INK_COLOR
            if "email_secondary_color" in company._fields and company.email_secondary_color in odoo_defaults:
                vals["email_secondary_color"] = BRAND_COLOR
            if vals:
                company.write(vals)
        self._resta_brand_websites()
        self._resta_debrand_mail_templates()
        return True

    @api.model
    def _resta_brand_websites(self):
        """Websites still on the «Your Logo» placeholder get the Resta logo (the restaurant can change it)."""
        if "website" not in self.env:
            return
        try:
            with file_open("website/static/src/img/website_logo.svg", "rb") as f:
                placeholder = base64.b64encode(f.read())
        except OSError:
            return
        websites = self.env["website"].sudo().with_context(bin_size=False).search([])
        targets = websites.filtered(lambda w: not w.logo or w.logo == placeholder)
        if targets:
            with file_open("resta_sidebar_theme/static/img/resta-logo.png", "rb") as f:
                targets.write({"logo": base64.b64encode(f.read())})

    @api.model
    def _resta_debrand_mail_templates(self):
        """Rewrite every language of the stored email bodies (translations included)."""
        if "mail.template" not in self.env:
            return 0
        self.env["mail.template"].flush_model(["body_html"])
        cr = self.env.cr
        cr.execute("SELECT id, body_html FROM mail_template WHERE body_html::text ILIKE %s", ["%odoo%"])
        changed = 0
        for template_id, body in cr.fetchall():
            if not isinstance(body, dict):
                continue
            new = {lang: debrand_html(value) for lang, value in body.items()}
            if new != body:
                cr.execute("UPDATE mail_template SET body_html = %s::jsonb WHERE id = %s",
                           [json.dumps(new), template_id])
                changed += 1
        if changed:
            self.env["mail.template"].invalidate_model(["body_html"])
        return changed


class MailTemplate(models.Model):
    _inherit = "mail.template"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("body_html"):
                vals["body_html"] = debrand_html(vals["body_html"])
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("body_html"):
            vals = dict(vals, body_html=debrand_html(vals["body_html"]))
        return super().write(vals)
