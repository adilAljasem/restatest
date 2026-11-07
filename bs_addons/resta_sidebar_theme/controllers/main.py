from odoo import http
from odoo.http import request

from ..models.ir_http import STATIC, _custom_static_logo


class RestaTheme(http.Controller):

    @http.route("/resta_theme/login_logo", type="http", auth="public", readonly=True)
    def login_logo(self, **kw):
        """Logo for the login page: uploaded sidebar logo, else brand file."""
        company = request.env["res.company"].sudo().search([], order="id", limit=1)
        if company and company.rs_logo_light:
            stream = request.env["ir.binary"]._get_image_stream_from(
                company, "rs_logo_light",
            )
            return stream.get_response(max_age=3600)
        return request.redirect(
            _custom_static_logo() or STATIC + "resta-wordmark-default.svg",
            code=302, local=True,
        )
