"""Sign-in coming from the Resta platform login page (account number + email + password).

The platform signs (account, timestamp) with this restaurant's token, so only a
form produced by resta.sa within the last 5 minutes is accepted here.
"""
import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

from odoo import http
from odoo.exceptions import AccessDenied
from odoo.http import request

_logger = logging.getLogger(__name__)
MAX_AGE = 300


class RestaAuth(http.Controller):

    def _back(self, back, error):
        icp = request.env["ir.config_parameter"].sudo()
        master = (icp.get_param("resta.master_url") or "").rstrip("/")
        account = icp.get_param("resta.account_number") or ""
        target = back if back and master and back.startswith(master + "/") else "%s/resta/login" % master
        sep = "&" if "?" in target else "?"
        return request.redirect(target + sep + urlencode({"error": error, "account": account}), local=False)

    @http.route("/resta/auth", type="http", auth="none", methods=["POST"], csrf=False, sitemap=False)
    def resta_auth(self, login=None, password=None, account=None, ts=None, sig=None, back=None, **kw):
        icp = request.env["ir.config_parameter"].sudo()
        token = icp.get_param("resta.tenant_token") or ""
        own_account = icp.get_param("resta.account_number") or ""
        try:
            fresh = abs(time.time() - int(ts or 0)) <= MAX_AGE
        except ValueError:
            fresh = False
        expected = hmac.new(token.encode(), ("%s:%s" % (account, ts)).encode(), hashlib.sha256).hexdigest()
        if not (token and account == own_account and fresh and sig and hmac.compare_digest(expected, sig)):
            return self._back(back, "expired")
        try:
            request.session.authenticate(request.env, {"login": login, "password": password, "type": "password"})
        except AccessDenied:
            return self._back(back, "auth")
        if not request.session.uid:  # two-factor authentication pending
            return request.redirect("/web/login/totp")
        return request.redirect("/odoo")
