"""API used by each restaurant database (resta_saas_tenant) to talk to the platform.

Authentication: account number + per-tenant token (set at provisioning).
Bodies and responses are plain JSON (no JSON-RPC envelope).
"""
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class RestaApi(http.Controller):

    def _payload(self):
        try:
            return json.loads(request.httprequest.get_data() or b"{}")
        except ValueError:
            return {}

    def _tenant(self, data):
        return request.env["resta.tenant"].sudo()._authenticate_api(data.get("account"), data.get("token"))

    @http.route("/resta/api/v1/sync", type="http", auth="public", methods=["POST"], csrf=False,
                save_session=False, sitemap=False)
    def sync(self, **kw):
        data = self._payload()
        tenant = self._tenant(data)
        if not tenant:
            return request.make_json_response({"error": "unauthorized"}, status=401)
        tenant._record_usage(data.get("usage"))
        return request.make_json_response(tenant._sync_response(data.get("last_id") or 0))

    @http.route("/resta/api/v1/feedback", type="http", auth="public", methods=["POST"], csrf=False,
                save_session=False, sitemap=False)
    def feedback(self, **kw):
        data = self._payload()
        tenant = self._tenant(data)
        if not tenant:
            return request.make_json_response({"error": "unauthorized"}, status=401)
        kind = data.get("kind") if data.get("kind") in ("suggestion", "rating", "support") else "suggestion"
        ann = request.env["resta.announcement"].sudo().browse(int(data.get("announcement_id") or 0)).exists()
        rating = str(data.get("rating") or "")
        fb = request.env["resta.feedback"].sudo().create({
            "tenant_id": tenant.id, "kind": kind, "announcement_id": ann.id or False,
            "rating": rating if rating in ("1", "2", "3") else False,
            "message": (data.get("message") or "")[:4000],
            "user_name": (data.get("user_name") or "")[:200], "user_email": (data.get("user_email") or "")[:200],
        })
        if kind in ("suggestion", "support") and tenant.account_manager_id:
            fb.activity_schedule("mail.mail_activity_data_todo", user_id=tenant.account_manager_id.id,
                                 summary="%s من %s" % (dict(fb._fields["kind"].selection)[kind], tenant.name))
        return request.make_json_response({"ok": True, "id": fb.id})
