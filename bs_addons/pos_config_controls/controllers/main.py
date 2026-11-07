from odoo import http
from odoo.http import request
from odoo.addons.point_of_sale.controllers.main import PosController


class RestrictedPosController(PosController):
    @http.route()
    def pos_web(self, config_id=False, from_backend=False, subpath=None, **kwargs):
        if config_id and request.env.user.has_group(
            "pos_config_controls.group_restricted_pos_user"
        ):
            config_id = int(config_id)
            allowed_config = request.env["pos.config"].search(
                [("id", "=", config_id)], limit=1
            )
            if not allowed_config:
                return request.redirect(
                    "/odoo/action-point_of_sale.action_client_pos_menu"
                )
        return super().pos_web(
            config_id=config_id,
            from_backend=from_backend,
            subpath=subpath,
            **kwargs,
        )
