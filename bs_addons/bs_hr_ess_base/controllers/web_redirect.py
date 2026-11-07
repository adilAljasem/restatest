from odoo import http
from odoo.http import request

from odoo.addons.portal.controllers.web import Home as PortalHome
from odoo.addons.web.controllers.utils import is_user_internal


def _ess_landing(uid):
    """'/my/hr' when the (portal) user is an employee and the company enables it."""
    if not uid or is_user_internal(uid):
        return None
    user = request.env['res.users'].sudo().browse(uid)
    employee = request.env['hr.employee']._ess_get_for_user(user)
    if employee and employee.company_id.ess_portal_landing:
        return '/my/hr'
    return None


class EssHome(PortalHome):
    """The Odoo mobile app (iOS / Android) opens /odoo or /web after login, which portal sends to /my:
    employees land directly on their self-service home instead."""

    @http.route()
    def index(self, *args, **kw):
        landing = _ess_landing(request.session.uid)
        if landing:
            return request.redirect(landing)
        return super().index(*args, **kw)

    @http.route()
    def web_client(self, s_action=None, **kw):
        landing = _ess_landing(request.session.uid)
        if landing:
            return request.redirect(landing)
        return super().web_client(s_action, **kw)

    def _login_redirect(self, uid, redirect=None):
        if not redirect or redirect in ('/my', '/odoo', '/web'):
            landing = _ess_landing(uid)
            if landing:
                redirect = landing
        return super()._login_redirect(uid, redirect=redirect)
