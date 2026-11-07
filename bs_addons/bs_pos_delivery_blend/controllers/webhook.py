import json
import logging
import uuid

from odoo import http
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from odoo.exceptions import AccessDenied
from odoo.http import request

from ..services.auth import BlendAuthService
from ..services.exceptions import BlendAPIError
from ..services.pos_api import BlendPOSAPIService
from ..services.webhook import BlendWebhookService
from odoo.addons.bs_pos_delivery_core.tools.http_audit import audit_failed_http_attempt

_logger = logging.getLogger(__name__)


def _audit_blend_connection():
    payload = request.httprequest.get_json(silent=True)
    if not isinstance(payload, dict):
        return False
    try:
        stores = request.env["bs.delivery.blend.webhook.service"].sudo()._resolve_stores(payload)
        authorized = BlendAuthService(request.env).authorized_stores(
            stores, request.httprequest.headers.get("Authorization", ""),
        )
    except BlendAPIError:
        return False
    connections = authorized.mapped("connection_id")
    return connections if len(connections) == 1 else False


def _json_response(payload, status=200, trace_id=None, extra_headers=None):
    """Keep the legacy wire format byte-for-byte compatible in structure."""
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
    def _positive_integer(value, field_name, default, maximum=100):
        if value in (None, ""):
            return default
        try:
            result = int(value)
        except (TypeError, ValueError):
            raise BlendAPIError("%s must be a positive integer." % field_name) from None
        if result < 1 or result > maximum:
            raise BlendAPIError("%s must be between 1 and %s." % (field_name, maximum))
        return result

    def _pos_service(self, trace_id, *, menu=False):
        auth = BlendAuthService(request.env)
        authorization = request.httprequest.headers.get("Authorization", "")
        if menu:
            configs, connection = auth.authorized_menu_scope(authorization)
        else:
            configs = auth.authorized_configs(authorization)
            connection = None
        return BlendPOSAPIService(
            auth.execution_environment(configs, authorization),
            configs,
            connection=connection,
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

    # -------------------------------------------------------------------------
    # Authentication (/api/login)
    # -------------------------------------------------------------------------
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
            return _json_response({"status": "Invalid JSON body"}, status=400, extra_headers=no_store)
        if not isinstance(payload, dict):
            return _json_response({"status": "Invalid request body"}, status=400, extra_headers=no_store)

        email = str(payload.get("email") or "").strip()
        password = payload.get("password")
        if not email or not isinstance(password, str) or not password:
            return _json_response({"status": "Credentials do not match"}, status=401, extra_headers=no_store)

        credential = {"type": "password", "login": email, "password": password}
        user_agent_env = {
            "interactive": False,
            "base_location": request.httprequest.url_root.rstrip("/"),
            "HTTP_HOST": request.httprequest.host,
            "REMOTE_ADDR": request.httprequest.remote_addr or "",
        }
        try:
            access_token = BlendAuthService(request.env).login_connection(email, password, user_agent_env)
            if access_token:
                return _json_response(
                    {"access_token": access_token}, extra_headers=no_store
                )
            auth_info = request.env["res.users"].authenticate(credential, user_agent_env)
        except AccessDenied:
            return _json_response({"status": "Credentials do not match"}, status=401, extra_headers=no_store)
        except BlendAPIError as error:
            return _json_response(
                {"status": error.message}, status=error.status_code, extra_headers=no_store
            )

        configs = BlendAuthService(request.env).legacy_login_configs(auth_info["uid"])
        if not configs:
            return _json_response({"status": "Credentials do not match"}, status=401, extra_headers=no_store)

        access_token = configs[0]._blend_issue_login_token()
        # Also sync hash to other linked configs for the same user
        for other_config in configs[1:]:
            other_config.sudo().write({
                "blend_login_token_hash": configs[0].blend_login_token_hash,
                "blend_login_token_issued_at": configs[0].blend_login_token_issued_at,
            })

        return _json_response({"access_token": access_token}, status=200, extra_headers=no_store)

    # -------------------------------------------------------------------------
    # Menu & Catalog (/api/menu, /api/branches)
    # -------------------------------------------------------------------------
    @http.route(
        "/api/menu",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        save_session=False,
        # The menu assigns per-product Blend modifier IDs on first publication.
        readonly=False,
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
                "modifier_group_id": request.httprequest.args.get("filter['modifier_group.id']"),
            }
            result = self._pos_service(trace_id, menu=True).get_menu(
                page=page,
                per=per,
                filters=filters,
                path=request.httprequest.base_url,
            )
            return _json_response(result, extra_headers=[("Cache-Control", "no-store")])
        except BlendAPIError as error:
            return self._pos_error(error, trace_id)
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            # Let Odoo roll back and replay the request instead of
            # answering 500 and losing the provider's update.
            raise
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
            return _json_response(result, extra_headers=[("Cache-Control", "no-store")])
        except BlendAPIError as error:
            return self._pos_error(error, trace_id)
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            # Let Odoo roll back and replay the request instead of
            # answering 500 and losing the provider's update.
            raise
        except Exception:
            _logger.exception("Blend Get Branches unexpected failure trace=%s", trace_id)
            return _json_response(
                {"success": False, "message": "Internal integration error."},
                status=500,
                trace_id=trace_id,
            )

    # -------------------------------------------------------------------------
    # Inbound Orders & Status Mutations (Route strictly via Delivery Core)
    # -------------------------------------------------------------------------
    @http.route(
        "/api/orders",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    @audit_failed_http_attempt("blend:create_order", _audit_blend_connection)
    def blend_receive_order(self, **kwargs):
        trace_id = uuid.uuid4().hex
        try:
            payload = request.httprequest.get_json(silent=False)
        except Exception:
            return _json_response(
                {"success": False, "message": "Invalid JSON body."},
                status=400,
                trace_id=trace_id,
            )
        try:
            result = request.env["bs.delivery.blend.intake"].sudo().receive_order(
                payload, request.httprequest.headers.get("Authorization", ""),
            )
            return _json_response(result, trace_id=trace_id)
        except BlendAPIError as error:
            _logger.warning(
                "Blend order rejected trace=%s status=%s error=%s",
                trace_id,
                error.status_code,
                error.message,
            )
            return _json_response(
                {"success": False, "message": error.message},
                status=error.status_code,
                trace_id=trace_id,
            )
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            # Let Odoo roll back and replay the request instead of
            # answering 500 and losing the provider's update.
            raise
        except Exception:
            _logger.exception("Blend order unexpected failure trace=%s", trace_id)
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
    @audit_failed_http_attempt("blend:update_order", _audit_blend_connection)
    def blend_update_order(self, **kwargs):
        trace_id = uuid.uuid4().hex
        try:
            payload = request.httprequest.get_json(silent=False)
        except Exception:
            return _json_response(
                {"success": False, "message": "Invalid JSON body."},
                status=400,
                trace_id=trace_id,
            )
        try:
            result = request.env["bs.delivery.blend.intake"].sudo().update_order(
                payload, request.httprequest.headers.get("Authorization", ""),
            )
            return _json_response(result, trace_id=trace_id)
        except BlendAPIError as error:
            _logger.warning(
                "Blend status rejected trace=%s status=%s error=%s",
                trace_id,
                error.status_code,
                error.message,
            )
            return _json_response(
                {"success": False, "message": error.message},
                status=error.status_code,
                trace_id=trace_id,
            )
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            # Let Odoo roll back and replay the request instead of
            # answering 500 and losing the provider's update.
            raise
        except Exception:
            _logger.exception("Blend status unexpected failure trace=%s", trace_id)
            return _json_response(
                {"success": False, "message": "Internal integration error."},
                status=500,
                trace_id=trace_id,
            )

    # -------------------------------------------------------------------------
    # Legacy Unified Webhook (/api/blend/webhook)
    # -------------------------------------------------------------------------
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
        headers = self._request_headers()
        try:
            result = BlendWebhookService(request.env).handle(
                payload,
                request.httprequest.headers.get("Authorization", ""),
                request_headers=headers,
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
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            # Let Odoo roll back and replay the request instead of
            # answering 500 and losing the provider's update.
            raise
        except Exception:
            _logger.exception("Blend webhook unexpected failure trace=%s", trace_id)
            return _json_response(
                {"status": "failed", "message": "Internal integration error."},
                status=500,
                trace_id=trace_id,
            )
