# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac

from odoo import http
from odoo.http import request
from odoo.tools.mimetypes import guess_mimetype

from ..models.tryorder_intake import TryOrderRequestError


class PublicProductImageController(http.Controller):
    """Serve products exported by an authorized TryOrder branch."""

    @http.route('/public/product/image/<int:product_id>', type='http', auth='public')
    def product_image(self, product_id, **kwargs):
        try:
            branch_id = int(kwargs.get('branch_id'))
        except (TypeError, ValueError):
            return request.not_found()
        token = kwargs.get('token')
        if not isinstance(token, str) or len(token) != 64:
            return request.not_found()
        stores = request.env['bs.delivery.store.binding'].sudo().search([
            ('pos_config_id', '=', branch_id), ('active', '=', True),
            ('connection_id.provider_id.code', '=', 'tryorder'),
            ('connection_id.active', '=', True),
            ('connection_id.state', 'in', ('connected', 'draft')),
        ])
        allowed = False
        for store in stores:
            connection = store.connection_id
            if connection.state == 'draft' and not (
                connection.environment == 'test' and connection.allow_draft_api_testing
            ):
                continue
            secret = connection.webhook_secret
            if not secret:
                continue
            expected = hmac.new(
                secret.encode(), f"{store.id}:{product_id}".encode(), hashlib.sha256,
            ).hexdigest()
            if hmac.compare_digest(token, expected):
                allowed = store
                break
        if not allowed:
            return request.not_found()
        try:
            request.env['bs.delivery.tryorder.intake'].sudo()._product(
                allowed, {'product_id': product_id},
            )
        except TryOrderRequestError:
            return request.not_found()
        product = request.env['product.template'].sudo().browse(product_id)
        if product.exists() and product.image_512:
            image_data = base64.b64decode(product.image_512)
            mimetype = guess_mimetype(image_data)
            if not mimetype or not mimetype.startswith('image/') or mimetype == 'image/svg+xml':
                return request.not_found()
            return request.make_response(image_data, [
                ('Content-Type', mimetype),
                ('Content-Length', len(image_data)),
            ])
        return request.not_found()
