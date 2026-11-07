import hmac
import time

from werkzeug.exceptions import NotFound

from odoo import _, http
from odoo.http import request

COOKIE = 'bs_ess_kiosk_%s'


class EssKiosk(http.Controller):
    """Branch screen showing a QR code that changes every N seconds.

    The page is public (a tablet in the branch, nobody logged in) but must be
    activated once with the branch kiosk key; activation is stored in a signed
    cookie. Codes are HMAC(secret, branch:step) and are never stored.
    """

    def _kiosk_location(self, token):
        if not token or len(token) < 10:
            raise NotFound()
        location = request.env['hr.work.location'].sudo().search([('ess_kiosk_token', '=', token)], limit=1)
        if not location or not location.ess_use_qr:
            raise NotFound()
        return location

    def _kiosk_activated(self, location):
        cookie = request.httprequest.cookies.get(COOKIE % location.id, '')
        return bool(cookie) and hmac.compare_digest(cookie, location._ess_kiosk_cookie_value())

    @http.route('/hr/kiosk/<string:token>', type='http', auth='public', website=True, sitemap=False,
                methods=['GET', 'POST'])
    def kiosk(self, token, key=None, **kw):
        location = self._kiosk_location(token)
        error = None
        if request.httprequest.method == 'POST':
            if key and hmac.compare_digest(key.strip(), location.sudo().ess_kiosk_key or ''):
                response = request.redirect('/hr/kiosk/%s' % token)
                response.set_cookie(COOKIE % location.id, location._ess_kiosk_cookie_value(),
                                    max_age=3600 * 24 * 365, httponly=True, samesite='Lax',
                                    secure=request.httprequest.scheme == 'https')
                return response
            error = _('Wrong activation key.')
        if not self._kiosk_activated(location):
            return request.render('bs_hr_ess_attendance.ess_kiosk_activate',
                                  {'location': location, 'token': token, 'error': error})
        return request.render('bs_hr_ess_attendance.ess_kiosk', {
            'location': location, 'token': token, 'period': max(location.ess_qr_period or 10, 5)})

    @http.route('/hr/kiosk/<string:token>/code', type='jsonrpc', auth='public', methods=['POST'])
    def kiosk_code(self, token, **kw):
        location = self._kiosk_location(token)
        if not self._kiosk_activated(location):
            return {'ok': False, 'message': _('Kiosk not activated.')}
        step = location._ess_qr_step()
        period = max(location.ess_qr_period or 10, 5)
        remaining = period - (time.time() % period)
        base = request.httprequest.url_root.rstrip('/')
        url = '%s/my/hr/attendance/qr?c=%s' % (base, location._ess_qr_code(step))
        return {'ok': True, 'value': url, 'remaining': remaining, 'period': period,
                'present': location.ess_today_present}
