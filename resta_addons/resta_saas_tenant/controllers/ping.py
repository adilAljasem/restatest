"""The platform asks this restaurant system to synchronise now (after an order or a plan change).

Signed like the sign-in: HMAC of (account, timestamp) with this restaurant's token.
"""
import hashlib
import hmac
import json
import time

from odoo import http
from odoo.http import request

MAX_AGE = 300


class RestaPing(http.Controller):

    @http.route("/resta/tenant/ping", type="http", auth="none", methods=["POST"], csrf=False,
                save_session=False, sitemap=False)
    def ping(self, **kw):
        try:
            data = json.loads(request.httprequest.get_data() or b"{}")
        except ValueError:
            data = {}
        icp = request.env["ir.config_parameter"].sudo()
        token = icp.get_param("resta.tenant_token") or ""
        account = icp.get_param("resta.account_number") or ""
        ts, sig = str(data.get("ts") or ""), str(data.get("sig") or "")
        try:
            fresh = abs(time.time() - int(ts)) <= MAX_AGE
        except ValueError:
            fresh = False
        expected = hmac.new(token.encode(), ("%s:%s" % (account, ts)).encode(), hashlib.sha256).hexdigest()
        if not (token and data.get("account") == account and fresh and hmac.compare_digest(expected, sig)):
            return request.make_json_response({"error": "unauthorized"}, status=401)
        ok = request.env["resta.saas.client"].sudo().sync()
        return request.make_json_response({"ok": bool(ok)})
