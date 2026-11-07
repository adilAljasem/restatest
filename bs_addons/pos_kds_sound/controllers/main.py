# -*- coding: utf-8 -*-
import base64
import mimetypes

from odoo import http
from odoo.http import request


class PosKdsSoundController(http.Controller):

    @http.route("/pos_kds_sound/config", type="http", auth="user", methods=["GET"])
    def kds_sound_config(self, **kw):
        settings = request.env["pos.kds.sound.settings"].sudo().get_settings()
        return request.make_json_response(settings.to_client_config())

    @http.route("/pos_kds_sound/sound", type="http", auth="user", methods=["GET"])
    def kds_sound_file(self, **kw):
        settings = request.env["pos.kds.sound.settings"].sudo().get_settings()
        if not settings.custom_sound:
            return request.not_found()
        content = base64.b64decode(settings.custom_sound)
        mimetype = mimetypes.guess_type(settings.custom_sound_name or "")[0] or "audio/mpeg"
        return request.make_response(content, headers=[
            ("Content-Type", mimetype),
            ("Content-Length", str(len(content))),
            ("Cache-Control", "private, max-age=86400"),
        ])
