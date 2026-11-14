"""Unified sign-in (like Foodics): account number + username + password.

The restaurant's own sign-in page asks for the account number too. When it is the number of
another restaurant, the person is sent to the platform's sign-in page (restaerp.com), which
opens the right system.
"""
import logging
import re
from urllib.parse import urlencode

from odoo import http
from odoo.http import request

from odoo.addons.web.controllers.home import Home

_logger = logging.getLogger(__name__)

ERRORS = {"account": "رقم الحساب غير صحيح."}


def _digits(value):
    return re.sub(r"\D", "", value or "")


class RestaLogin(Home):

    def _resta_account(self):
        if not request.db:
            return ""
        try:
            return request.env["ir.config_parameter"].sudo().get_param("resta.account_number") or ""
        except Exception:  # noqa: BLE001 - no database selected yet: plain page
            return ""

    @http.route()
    def web_login(self, redirect=None, **kw):
        own = self._resta_account()
        given = None
        if own and request.httprequest.method == "POST" and "account" in kw:
            given = _digits(kw.get("account"))
            if given != own:
                master = (request.env["ir.config_parameter"].sudo().get_param("resta.master_url") or "").rstrip("/")
                if given and master:
                    query = urlencode({"account": given, "email": (kw.get("login") or "").strip()})
                    return request.redirect("%s/resta/login?%s" % (master, query), local=False)
                query = {"resta_error": "account"}
                if redirect:
                    query["redirect"] = redirect
                return request.redirect("/web/login?" + urlencode(query))
        response = super().web_login(redirect=redirect, **kw)
        if own and getattr(response, "is_qweb", False):
            response.qcontext.update({
                "resta_account_number": own,
                "resta_account": given or own,
                "auth_subtitle": "أدخل رقم الحساب واسم المستخدم وكلمة المرور.",
            })
            if not response.qcontext.get("error") and kw.get("resta_error") in ERRORS:
                response.qcontext["error"] = ERRORS[kw["resta_error"]]
        return response
