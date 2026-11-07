import hashlib
import json
import re

from markupsafe import Markup

from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request
from odoo.tools import file_open


HEX_COLOR = re.compile(r'^#[0-9A-Fa-f]{3,8}$')


_ASSET_VERSIONS = {}


def _asset_version(path):
    """Content hash of a static file, used as ?v= so phones never run an old cached copy."""
    if path not in _ASSET_VERSIONS:
        with file_open(path, 'rb') as f:
            _ASSET_VERSIONS[path] = hashlib.sha1(f.read()).hexdigest()[:10]
    return _ASSET_VERSIONS[path]


def _script_json(value):
    """JSON safe to inline inside a <script> element."""
    return Markup(json.dumps(value).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026'))


class PosOrderTrackingController(http.Controller):

    def _get_order(self, token):
        if not token or len(token) < 16 or len(token) > 64:
            raise NotFound()
        order = request.env['pos.order'].sudo().search([('tracking_token', '=', token)], limit=1)
        if not order or not order._tracking_is_eligible():
            raise NotFound()
        return order

    def _json(self, data, status=200):
        return request.make_response(
            json.dumps(data),
            headers=[('Content-Type', 'application/json'), ('Cache-Control', 'no-store')],
            status=status,
        )

    # ------------------------------------------------------------------
    # Service worker and web manifest (scope: /track/)
    # ------------------------------------------------------------------
    @http.route('/track/sw.js', type='http', auth='public', methods=['GET'], sitemap=False)
    def tracking_service_worker(self):
        with file_open('pos_order_tracking_nps/static/src/page/sw.js', 'rb') as f:
            body = f.read()
        return request.make_response(body, headers=[
            ('Content-Type', 'text/javascript; charset=utf-8'),
            ('Service-Worker-Allowed', '/track/'),
            ('Cache-Control', 'no-cache'),
        ])

    @http.route('/track/<string:token>/manifest.webmanifest', type='http', auth='public', methods=['GET'], sitemap=False)
    def tracking_manifest(self, token):
        order = self._get_order(token)
        config = order.config_id
        manifest = {
            'name': order.company_id.name,
            'short_name': (order.company_id.name or '')[:12],
            'start_url': f'/track/{token}',
            'scope': '/track/',
            'display': 'standalone',
            'background_color': '#ffffff',
            'theme_color': config.tracking_color or '#0F766E',
            'icons': [{'src': f'/logo.png?company={order.company_id.id}', 'sizes': '192x192', 'type': 'image/png'}],
        }
        return request.make_response(json.dumps(manifest), headers=[('Content-Type', 'application/manifest+json')])

    # ------------------------------------------------------------------
    # Customer page
    # ------------------------------------------------------------------
    @http.route('/track/<string:token>', type='http', auth='public', methods=['GET'], sitemap=False)
    def tracking_page(self, token, **kw):
        order = self._get_order(token)
        order._tracking_sync()
        data = order._tracking_public_data()
        company = order.company_id
        color = data['shop']['color']
        if not HEX_COLOR.match(color or ''):
            color = '#0F766E'
        response = request.render('pos_order_tracking_nps.tracking_page', {
            'order': order,
            'token': token,
            'company': company,
            'has_logo': bool(company.logo),
            'color': color,
            'brand_css': Markup('.trk{--brand:%s}' % color),
            'initial_data': _script_json(data),
            # Alerts are mandatory while the order is being prepared (see the gate in the page).
            'gate': bool(data['features']['pager']) and data['state'] in ('received', 'preparing'),
            'token_json': _script_json(token),
            'js_version': _asset_version('pos_order_tracking_nps/static/src/page/tracking.js'),
            'css_version': _asset_version('pos_order_tracking_nps/static/src/page/tracking.css'),
            'vapid_json': _script_json(request.env['pos.tracking.subscription'].sudo()._vapid_public_key() or ''),
        })
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Robots-Tag'] = 'noindex'
        return response

    @http.route('/track/<string:token>/status', type='http', auth='public', methods=['GET'], sitemap=False)
    def tracking_status(self, token):
        order = self._get_order(token)
        if order.tracking_state not in ('completed', 'cancelled'):
            order._tracking_sync()
        return self._json(order._tracking_public_data())

    @http.route('/track/<string:token>/nps', type='jsonrpc', auth='public', methods=['POST'])
    def tracking_nps(self, token, score, comment=None):
        order = self._get_order(token)
        try:
            score = int(score)
        except (TypeError, ValueError):
            return {'ok': False, 'error': 'invalid_score'}
        if not 0 <= score <= 10:
            return {'ok': False, 'error': 'invalid_score'}
        ok = order._tracking_submit_nps(score, comment if isinstance(comment, str) else None)
        return {'ok': ok, 'data': order._tracking_public_data()}

    @http.route('/track/<string:token>/ack', type='http', auth='public', methods=['POST'], csrf=False, sitemap=False)
    def tracking_ack(self, token, **kw):
        """The customer saw the 'ready' alert: stop repeating it. Called by the page and the service worker."""
        order = self._get_order(token)
        if order.tracking_state in ('ready', 'completed'):
            order._tracking_ack_ready()
        return self._json({'ok': True})

    @http.route('/track/<string:token>/subscribe', type='jsonrpc', auth='public', methods=['POST'])
    def tracking_subscribe(self, token, subscription, lang=None):
        order = self._get_order(token)
        endpoint = (subscription or {}).get('endpoint')
        keys = (subscription or {}).get('keys') or {}
        if not endpoint or not endpoint.startswith('https://') or not keys.get('p256dh') or not keys.get('auth'):
            return {'ok': False}
        Sub = request.env['pos.tracking.subscription'].sudo()
        # One phone follows one order: this browser's notifications move to the new order,
        # so it no longer rings or asks for a rating about a previous one.
        Sub.search([('endpoint', '=', endpoint[:2000]), ('order_id', '!=', order.id)]).unlink()
        existing = Sub.search([('order_id', '=', order.id), ('endpoint', '=', endpoint)], limit=1)
        vals = {'keys': json.dumps({'p256dh': keys['p256dh'], 'auth': keys['auth']}),
                'lang': 'en' if (lang or '').startswith('en') else 'ar'}
        if existing:
            existing.write(vals)
        else:
            if Sub.search_count([('order_id', '=', order.id)]) >= 5:
                return {'ok': False}
            Sub.create({'order_id': order.id, 'endpoint': endpoint[:2000], **vals})
        return {'ok': True}
