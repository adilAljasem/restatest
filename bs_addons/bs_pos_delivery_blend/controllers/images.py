from odoo import http
from odoo.exceptions import UserError
from odoo.http import request


class BlendImageController(http.Controller):
    @http.route(
        "/api/blend/products/<int:product_id>/image",
        type="http",
        auth="public",
        methods=["GET"],
        readonly=True,
        save_session=False,
    )
    def product_image(self, product_id, **kwargs):
        product = request.env["product.template"]._blend_public_image_product(product_id)
        if not product:
            raise request.not_found()
        try:
            stream = request.env["ir.binary"]._get_stream_from(
                product,
                "image_1920",
                filename="blend-product-%s" % product.id,
            )
            if stream.type not in ("data", "path") or not stream.size or not stream.mimetype.startswith("image/"):
                raise request.not_found()
            return stream.get_response(as_attachment=False, immutable=False, max_age=0)
        except (UserError, FileNotFoundError):
            raise request.not_found() from None

