import json
import logging
import uuid

from odoo import http
from odoo.exceptions import AccessDenied
from odoo.http import request

from ..services.auth import BlendAuthService
from ..services.exceptions import BlendAPIError
from ..services.pos_api import BlendPOSAPIService
from ..services.webhook import BlendWebhookService


_logger = logging.getLogger(__name__)


def _json_response(payload, status=200, trace_id=None, extra_headers=None):
    body = dict(payload)
    if trace_id:
        body["trace_id"] = trace_id
    headers = [("Content-Type", "application/json; charset=utf-8")]
    headers.extend(extra_headers or [])
    return request.make_response(
        json.dumps(body, ensure_ascii=False, default=str),
        headers=headers,
        status=status,
    )


class BlendWebhookController(http.Controller):
    @staticmethod
    def _request_headers():
        return {
            "Content-Type": request.httprequest.headers.get("Content-Type", ""),
            "User-Agent": request.httprequest.headers.get("User-Agent", ""),
            "X-Request-ID": request.httprequest.headers.get("X-Request-ID", ""),
        }

    @staticmethod
    def _json_body():
        try:
            return request.httprequest.get_json(silent=False)
        except Exception:
            raise BlendAPIError("Invalid JSON body.") from None

    @staticmethod
    def _positive_integer(value, field_name, default, maximum=100):
        if value in (None, ""):
            return default
        try:
            result = int(value)
        except (TypeError, ValueError):
            raise BlendAPIError("%s must be a positive integer." % field_name) from None
        if result < 1 or result > maximum:
            raise BlendAPIError(
                "%s must be between 1 and %s." % (field_name, maximum)
            )
        return result

    def _pos_service(self, trace_id):
        configs = BlendAuthService(request.env).authorized_configs(
            request.httprequest.headers.get("Authorization", "")
        )
        return BlendPOSAPIService(
            request.env,
            configs,
            request_headers=self._request_headers(),
            request_id=trace_id,
        )

    @staticmethod
    def _pos_error(error, trace_id):
        _logger.warning(
            "Blend POS API rejected trace=%s status=%s error=%s",
            trace_id,
            error.status_code,
            error.message,
        )
        return _json_response(
            {"success": False, "message": error.message},
            status=error.status_code,
            trace_id=trace_id,
            extra_headers=[("Cache-Control", "no-store")],
        )

    @http.route(
        "/api/login",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def blend_login(self, **kwargs):
        no_store = [("Cache-Control", "no-store"), ("Pragma", "no-cache")]
        try:
            payload = request.httprequest.get_json(silent=False)
        except Exception:
            return _json_response(
                {"status": "Invalid JSON body"},
                status=400,
                extra_headers=no_store,
            )
        if not isinstance(payload, dict):
            return _json_response(
                {"status": "Invalid request body"},
                status=400,
                extra_headers=no_store,
            )

        email = str(payload.get("email") or "").strip()
        password = payload.get("password")
        if not email or not isinstance(password, str) or not password:
            return _json_response(
                {"status": "Credentials do not match"},
                status=401,
                extra_headers=no_store,
            )

        credential = {"type": "password", "login": email, "password": password}
        user_agent_env = {
            "interactive": False,
            "base_location": request.httprequest.url_root.rstrip("/"),
            "HTTP_HOST": request.httprequest.host,
            "REMOTE_ADDR": request.httprequest.remote_addr or "",
        }
        try:
            auth_info = request.env["res.users"].authenticate(
                credential,
                user_agent_env,
            )
        except AccessDenied:
            return _json_response(
                {"status": "Credentials do not match"},
                status=401,
                extra_headers=no_store,
            )

        configs = request.env["pos.config"].sudo().search(
            [
                ("blend_enabled", "=", True),
                ("blend_login_user_id", "=", auth_info["uid"]),
            ]
        )
        if not configs:
            return _json_response(
                {"status": "Credentials do not match"},
                status=401,
                extra_headers=no_store,
            )

        access_token = configs._blend_issue_login_token()
        return _json_response(
            {"access_token": access_token},
            status=200,
            extra_headers=no_store,
        )

    @http.route(
        "/api/blend/webhook",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def blend_webhook(self, **kwargs):
        trace_id = uuid.uuid4().hex
        try:
            payload = request.httprequest.get_json(silent=False)
        except Exception:
            return _json_response(
                {"status": "failed", "message": "Invalid JSON body."},
                status=400,
                trace_id=trace_id,
            )
        try:
            result = BlendWebhookService(request.env).handle(
                payload,
                request.httprequest.headers.get("Authorization", ""),
                request_headers=self._request_headers(),
            )
            return _json_response(result, status=200, trace_id=trace_id)
        except BlendAPIError as error:
            _logger.warning(
                "Blend webhook rejected trace=%s status=%s error=%s",
                trace_id,
                error.status_code,
                error.message,
            )
            return _json_response(
                {"status": "failed", "message": error.message},
                status=error.status_code,
                trace_id=trace_id,
            )
        except Exception:
            _logger.exception("Blend webhook unexpected failure trace=%s", trace_id)
            return _json_response(
                {"status": "failed", "message": "Internal integration error."},
                status=500,
                trace_id=trace_id,
            )

    @http.route(
        "/api/menu",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def blend_get_menu(self, **kwargs):
        trace_id = uuid.uuid4().hex
        try:
            page = self._positive_integer(
                request.httprequest.args.get("page"), "page", 1, maximum=1000000
            )
            per = self._positive_integer(
                request.httprequest.args.get("per"), "per", 25, maximum=100
            )
            filters = {
                "product_id": request.httprequest.args.get("filter['product_id']"),
                "category_id": request.httprequest.args.get("filter['category.id']"),
                "modifier_group_id": request.httprequest.args.get(
                    "filter['modifier_group.id']"
                ),
            }
            result = self._pos_service(trace_id).get_menu(
                page=page,
                per=per,
                filters=filters,
                path=request.httprequest.base_url,
            )
            return _json_response(
                result,
                extra_headers=[("Cache-Control", "no-store")],
            )
        except BlendAPIError as error:
            return self._pos_error(error, trace_id)
        except Exception:
            _logger.exception("Blend Get Menu unexpected failure trace=%s", trace_id)
            return _json_response(
                {"success": False, "message": "Internal integration error."},
                status=500,
                trace_id=trace_id,
            )

    @http.route(
        "/api/branches",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def blend_get_branches(self, **kwargs):
        trace_id = uuid.uuid4().hex
        try:
            result = self._pos_service(trace_id).get_branches()
            return _json_response(
                result,
                extra_headers=[("Cache-Control", "no-store")],
            )
        except BlendAPIError as error:
            return self._pos_error(error, trace_id)
        except Exception:
            _logger.exception("Blend Get Branches unexpected failure trace=%s", trace_id)
            return _json_response(
                {"success": False, "message": "Internal integration error."},
                status=500,
                trace_id=trace_id,
            )

    @http.route(
        "/api/orders",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def blend_create_order(self, **kwargs):
        trace_id = uuid.uuid4().hex
        try:
            result = self._pos_service(trace_id).create_order(self._json_body())
            return _json_response(
                result,
                extra_headers=[("Cache-Control", "no-store")],
            )
        except BlendAPIError as error:
            return self._pos_error(error, trace_id)
        except Exception:
            _logger.exception("Blend Create Order unexpected failure trace=%s", trace_id)
            return _json_response(
                {"success": False, "message": "Internal integration error."},
                status=500,
                trace_id=trace_id,
            )

    @http.route(
        "/api/order/update",
        type="http",
        auth="public",
        methods=["PUT"],
        csrf=False,
        save_session=False,
    )
    def blend_update_order(self, **kwargs):
        trace_id = uuid.uuid4().hex
        try:
            result = self._pos_service(trace_id).update_order(self._json_body())
            return _json_response(
                result,
                extra_headers=[("Cache-Control", "no-store")],
            )
        except BlendAPIError as error:
            return self._pos_error(error, trace_id)
        except Exception:
            _logger.exception("Blend Update Order unexpected failure trace=%s", trace_id)
            return _json_response(
                {"success": False, "message": "Internal integration error."},
                status=500,
                trace_id=trace_id,
            )
