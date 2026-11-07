import json

from odoo import http
from odoo.http import request

SERVICE_WORKER = """
const CACHE = 'bs-ess-v1';
const OFFLINE_URL = '/my/hr/offline';
self.addEventListener('install', (event) => {
    event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll([OFFLINE_URL])));
    self.skipWaiting();
});
self.addEventListener('activate', (event) => {
    event.waitUntil(caches.keys().then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
    self.clients.claim();
});
self.addEventListener('fetch', (event) => {
    const req = event.request;
    if (req.method !== 'GET') { return; }
    const url = new URL(req.url);
    if (req.mode === 'navigate') {
        event.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)));
        return;
    }
    if (url.pathname.startsWith('/web/assets/') || url.pathname.includes('/static/')) {
        event.respondWith(caches.open(CACHE).then((cache) => cache.match(req).then((hit) =>
            hit || fetch(req).then((resp) => { if (resp.ok) { cache.put(req, resp.clone()); } return resp; }))));
    }
});
"""


class EssPwa(http.Controller):

    @http.route('/my/hr/manifest.webmanifest', type='http', auth='public', website=True, sitemap=False)
    def ess_manifest(self, **kw):
        company = request.env.user.company_id if not request.env.user._is_public() \
            else request.env['res.company'].sudo().search([], limit=1)
        color = company.ess_brand_color or '#FF5A1F'
        manifest = {
            'name': company.ess_app_name or 'Employee Self-Service',
            'short_name': (company.ess_app_name or 'ESS')[:12],
            'start_url': '/my/hr',
            'scope': '/my/hr',
            'display': 'standalone',
            'background_color': '#ffffff',
            'theme_color': color,
            'dir': 'auto',
            'icons': [
                {'src': '/web/image/res.company/%s/logo/192x192' % company.id, 'sizes': '192x192',
                 'type': 'image/png'},
                {'src': '/web/image/res.company/%s/logo/512x512' % company.id, 'sizes': '512x512',
                 'type': 'image/png'},
            ],
        }
        return request.make_response(json.dumps(manifest),
                                     headers=[('Content-Type', 'application/manifest+json')])

    @http.route('/my/hr/sw.js', type='http', auth='public', sitemap=False)
    def ess_service_worker(self, **kw):
        return request.make_response(SERVICE_WORKER, headers=[
            ('Content-Type', 'text/javascript'),
            ('Service-Worker-Allowed', '/my/hr'),
            ('Cache-Control', 'no-cache'),
        ])

    @http.route('/my/hr/offline', type='http', auth='public', website=True, sitemap=False)
    def ess_offline(self, **kw):
        return request.render('bs_hr_ess_base.ess_offline', {})
