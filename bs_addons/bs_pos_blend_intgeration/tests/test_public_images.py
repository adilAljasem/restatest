import base64
from io import BytesIO
from unittest.mock import patch
from urllib.parse import urlsplit

from PIL import Image

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import HttpCase, tagged

from ..services.pos_api import BlendPOSAPIService


@tagged("post_install", "-at_install")
class TestBlendPublicImages(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("ar_001")
        cls.category = cls.env["pos.category"].create({"name": "Public Blend images"})
        cls.config = cls.env["pos.config"].create({
            "name": "Public Blend image test",
            "blend_enabled": True,
            "blend_branch_id": "81001",
            "blend_brand_id": "81000",
            "blend_inbound_token": "public-image-test-token",
            "limit_categories": True,
            "iface_available_categ_ids": [Command.set(cls.category.ids)],
            "payment_method_ids": [Command.clear()],
        })
        cls.products = cls.env["product.template"].with_context(blend_inbound_sync=True)
        buffer = BytesIO()
        Image.new("RGB", (32, 24), "blue").save(buffer, format="PNG")
        cls.image_values = {
            "name": "Blend image product",
            "company_id": cls.env.company.id,
            "available_in_pos": True,
            "sale_ok": True,
            "pos_categ_ids": [Command.set(cls.category.ids)],
            "image_1920": base64.b64encode(buffer.getvalue()),
        }
        cls.product = cls.products.create({
            **cls.image_values,
            "description_purchase": "PRIVATE-PURCHASE-NOTE",
        })
        cls.expected_image = base64.b64decode(cls.product.image_1920)
        cls.env["ir.config_parameter"].sudo().set_param("web.base.url", cls.base_url())

    def _image_url(self, product=None):
        product = self.product if product is None else product
        return BlendPOSAPIService(self.env, self.config)._product_image_url(product)

    def _get_public_image(self, product=None, *, url=None, params=None, headers=None):
        # Keep only Odoo's test-cursor cookie, never a logged-in browser session.
        self.opener.cookies.pop("session_id", None)
        product = self.product if product is None else product
        if url is None:
            url = "/api/blend/products/%s/image" % product.id
        parsed = urlsplit(url)
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        return self.url_open(
            path,
            headers={"X-Odoo-Database": self.env.cr.dbname, **(headers or {})},
            params=params,
            allow_redirects=False,
        )

    def test_public_route_contract(self):
        rule = next(
            rule for rule in self.env["ir.http"].routing_map().iter_rules()
            if str(rule) == "/api/blend/products/<int:product_id>/image"
        )
        self.assertEqual(rule.endpoint.routing["auth"], "public")
        self.assertEqual(set(rule.endpoint.routing["methods"]), {"GET"})
        self.assertTrue(rule.endpoint.routing["readonly"])
        self.assertFalse(rule.endpoint.routing["save_session"])

    def test_menu_image_url_downloads_real_image_without_login_or_token(self):
        menu = self.url_open(
            "/api/menu",
            headers={
                "X-Odoo-Database": self.env.cr.dbname,
                "Authorization": "Bearer public-image-test-token",
            },
            params={"filter['product_id']": self.product.id},
        )
        self.assertEqual(menu.status_code, 200, menu.text)
        image_url = menu.json()["categories"][0]["products"][0]["image_url"]
        self.assertTrue(image_url.startswith(self.base_url() + "/api/blend/products/"))
        response = self._get_public_image(url=image_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.expected_image)
        self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertEqual(Image.open(BytesIO(response.content)).size, (32, 24))
        self.assertNotIn("Authorization", response.request.headers)
        self.assertNotIn("session_id", response.request.headers.get("Cookie", ""))
        self.assertNotIn("Set-Cookie", response.headers)

    def test_product_data_and_raw_attachment_stay_private(self):
        public = self.env.ref("base.public_user")
        with self.assertRaises(AccessError):
            self.product.with_user(public).sudo(False).read(["description_purchase"])
        attachment = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "product.template"),
            ("res_id", "=", self.product.id),
            ("res_field", "=", "image_1920"),
        ])
        self.assertFalse(attachment.public)
        with self.assertRaises(AccessError):
            self.env["ir.binary"].with_user(public).sudo(False)._find_record(
                res_model="ir.attachment", res_id=attachment.id, field="raw",
            )

    def test_private_non_pos_non_sale_and_modifier_products_are_denied(self):
        for values in (
            {"available_in_pos": False}, {"sale_ok": False},
            {"use_as_modifier": True}, {"pos_categ_ids": [Command.clear()]},
        ):
            with self.subTest(values=values):
                product = self.products.create({**self.image_values, **values})
                # Apply the final publication state explicitly after creation.
                if values.get("available_in_pos") is False:
                    product.write({"available_in_pos": False})
                    self.assertFalse(product.available_in_pos)
                self.assertEqual(self._get_public_image(product).status_code, 404)

    def test_company_and_branch_mapping_are_enforced(self):
        company = self.env["res.company"].create({"name": "Private image company"})
        foreign = self.products.create({**self.image_values, "company_id": company.id})
        self.assertEqual(self._get_public_image(foreign).status_code, 404)
        private_branch = self.env["pos.config"].create({
            "name": "Disabled image branch", "payment_method_ids": [Command.clear()],
        })
        mapped = self.products.create({**self.image_values, "blend_config_id": private_branch.id})
        self.assertEqual(self._get_public_image(mapped).status_code, 404)

    def test_shared_and_out_of_stock_menu_images_are_public(self):
        self.product.write({
            "company_id": False,
            "blend_unavailable_config_ids": [Command.link(self.config.id)],
        })
        self.assertEqual(self._get_public_image().content, self.expected_image)

    def test_disabling_branch_revokes_access_even_for_cached_url(self):
        old_url = self._image_url()
        response = self._get_public_image(url=old_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("max-age=0", response.headers["Cache-Control"])
        self.config.write({"blend_enabled": False})
        response = self._get_public_image(url=old_url, headers={"If-None-Match": response.headers["ETag"]})
        self.assertEqual(response.status_code, 404)

    def test_removing_product_from_menu_revokes_access(self):
        old_url = self._image_url()
        self.product.write({"available_in_pos": False})
        self.assertEqual(self._get_public_image(url=old_url).status_code, 404)

    def test_image_replacement_changes_url_and_serves_new_bytes(self):
        old_url = self._image_url()
        old_response = self._get_public_image(url=old_url)
        buffer = BytesIO()
        Image.new("RGB", (40, 30), "red").save(buffer, format="PNG")
        self.product.write({"image_1920": base64.b64encode(buffer.getvalue())})
        new_url = self._image_url()
        self.assertNotEqual(new_url, old_url)
        response = self._get_public_image(url=new_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, base64.b64decode(self.product.image_1920))
        self.assertNotEqual(response.headers["ETag"], old_response.headers["ETag"])

    def test_caller_cannot_choose_another_model_field_or_filename(self):
        response = self._get_public_image(params={
            "model": "res.users", "id": self.env.user.id,
            "field": "description_purchase", "filename_field": "description_purchase",
            "mimetype": "text/html", "download": "1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.expected_image)
        self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertNotIn("PRIVATE-PURCHASE-NOTE", response.headers.get("Content-Disposition", ""))

    def test_missing_record_and_missing_image_return_404_not_placeholder(self):
        self.assertEqual(self._get_public_image(url="/api/blend/products/0/image").status_code, 404)
        self.product.write({"image_1920": False})
        self.assertEqual(self._image_url(), "")
        self.assertEqual(self._get_public_image().status_code, 404)

    def test_missing_filestore_file_returns_404(self):
        binary = type(self.env["ir.binary"])
        with patch.object(binary, "_get_stream_from", side_effect=FileNotFoundError):
            self.assertEqual(self._get_public_image().status_code, 404)
