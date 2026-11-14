import base64
import json

from lxml import etree

from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools.misc import file_open

from odoo.addons.resta_brand.models.brand import debrand_html


@tagged("post_install", "-at_install")
class TestBrand(TransactionCase):

    def test_debrand_html(self):
        html = ('<div>Powered by <a href="https://www.odoo.com?utm_source=db&amp;utm_medium=email" style="x">Odoo</a>'
                '<img alt="Odoo" src="/web/static/img/logo_inverse_white_206px.png"/></div>')
        out = debrand_html(html)
        self.assertNotIn("odoo", out.lower())
        self.assertIn('href="https://resta.sa"', out)
        self.assertIn(">Resta</a>", out)
        self.assertIn("resta-logo-white.png", out)
        self.assertEqual(debrand_html("<p>Hello</p>"), "<p>Hello</p>")

    def test_website_placeholder_logo(self):
        if "website" not in self.env:
            self.skipTest("website is not installed")
        with file_open("website/static/src/img/website_logo.svg", "rb") as f:
            placeholder = base64.b64encode(f.read())
        own = base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        site = self.env["website"].create({"name": "placeholder", "logo": placeholder})
        custom = self.env["website"].create({"name": "custom", "logo": own})
        self.env["res.company"]._resta_brand_apply()
        self.assertNotEqual(site.with_context(bin_size=False).logo, placeholder)
        self.assertEqual(custom.with_context(bin_size=False).logo, own)

    def test_stored_email_templates_and_bot(self):
        template = self.env["mail.template"].create({
            "name": "t", "model_id": self.env.ref("base.model_res_partner").id, "body_html": "<p>x</p>"})
        self.env.cr.execute("UPDATE mail_template SET body_html = %s::jsonb WHERE id = %s", [json.dumps({
            "en_US": '<p>Powered by <a href="https://www.odoo.com">Odoo</a></p>',
            "ar_001": '<p>مشغّل بواسطة <a href="https://www.odoo.com">Odoo</a></p>'}), template.id])
        self.env["mail.template"].invalidate_model(["body_html"])
        self.env["res.company"]._resta_brand_apply()
        self.env.cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [template.id])
        body = self.env.cr.fetchone()[0]
        self.assertTrue(all("odoo" not in v.lower() and "resta.sa" in v for v in body.values()), body)
        self.assertEqual(self.env.ref("base.partner_root").name, "Resta")
        self.assertEqual(self.env["ir.config_parameter"].sudo().get_param("web.web_app_name"), "Resta")
        # new templates are cleaned when created
        new = self.env["mail.template"].create({
            "name": "n", "model_id": self.env.ref("base.model_res_partner").id,
            "body_html": '<a href="https://www.odoo.com">Odoo</a>'})
        self.assertNotIn("odoo.com", new.body_html)

    def test_no_odoo_link_left_in_branded_templates(self):
        for xmlid in ("web.login_layout", "web.brand_promotion_message", "mail.mail_notification_layout",
                      "mail.mail_notification_light", "auth_signup.reset_password_email",
                      "portal.portal_record_sidebar"):
            view = self.env.ref(xmlid)
            arch = etree.tostring(view._get_combined_arch(), encoding="unicode")
            self.assertNotIn("www.odoo.com", arch, xmlid)
            if "website.layout" not in arch:  # with the website, the login page uses the website layout
                # the sign-in pages call the Resta page (resta_brand.auth_shell), which carries the link
                self.assertTrue("resta.sa" in arch or "resta_brand.auth_shell" in arch, xmlid)


@tagged("post_install", "-at_install")
class TestBrandHttp(HttpCase):

    def test_web_app_manifest(self):
        data = self.url_open("/web/manifest.webmanifest").json()
        self.assertEqual(data["name"], "Resta")
        self.assertTrue(all("resta" in icon["src"] for icon in data["icons"]))

    def test_login_page(self):
        html = self.url_open("/web/login").text
        self.assertNotIn("www.odoo.com", html)
        self.assertIn("resta.sa", html)
        self.assertIn("rs_auth_brand", html)
        self.assertIn("اسم المستخدم", html)
