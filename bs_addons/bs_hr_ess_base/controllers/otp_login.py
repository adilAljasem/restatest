import logging

from odoo import _, http
from odoo.exceptions import AccessDenied
from odoo.http import request

_logger = logging.getLogger(__name__)


class EssOtpLogin(http.Controller):

    def _otp_enabled(self):
        return bool(request.env['res.company'].sudo().search_count([('ess_otp_login', '=', True)]))

    @http.route('/my/hr/login', type='http', auth='public', website=True, sitemap=False,
                methods=['GET', 'POST'])
    def ess_otp_login(self, phone=None, **kw):
        if not self._otp_enabled():
            return request.redirect('/web/login?redirect=/my/hr')
        if not request.env.user._is_public():
            return request.redirect('/my/hr')
        values = {'phone': phone or '', 'error': None, 'step': 'phone'}
        if request.httprequest.method == 'POST' and phone:
            employee = request.env['bs.hr.ess.otp'].sudo()._find_employee_by_phone(phone)
            # Same answer whether the number exists or not (no user enumeration).
            values['step'] = 'code'
            if employee:
                ip = request.httprequest.remote_addr
                otp, error = request.env['bs.hr.ess.otp'].sudo()._issue(employee, ip)
                if error:
                    values.update(step='phone', error=error)
                elif otp:
                    request.session['ess_otp'] = {'otp_id': otp.id, 'login': employee.user_id.login}
            else:
                request.session['ess_otp'] = {'otp_id': 0, 'login': ''}
        return request.render('bs_hr_ess_base.ess_login', values)

    @http.route('/my/hr/login/verify', type='http', auth='public', website=True, sitemap=False,
                methods=['POST'])
    def ess_otp_verify(self, code=None, **kw):
        pending = request.session.get('ess_otp') or {}
        values = {'phone': '', 'step': 'code', 'error': None}
        if not pending.get('otp_id') or not pending.get('login'):
            values['error'] = _('Invalid or expired code.')
            return request.render('bs_hr_ess_base.ess_login', values)
        credential = {'type': 'bs_ess_otp', 'login': pending['login'],
                      'otp_id': pending['otp_id'], 'code': (code or '').strip()}
        try:
            request.session.authenticate(request.env, credential)
        except AccessDenied:
            values['error'] = _('Invalid or expired code.')
            return request.render('bs_hr_ess_base.ess_login', values)
        request.session.pop('ess_otp', None)
        return request.redirect('/my/hr')
