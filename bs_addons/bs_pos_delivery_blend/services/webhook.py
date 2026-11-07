import hashlib
import hmac
import json
import time
import uuid

from odoo import fields, models

from odoo.addons.bs_pos_delivery_core.tools import serialize_masked_payload
from odoo.addons.bs_pos_delivery_core.exceptions import DeliveryAuthenticationError, DeliveryWebhookConflictError

from .auth import BlendAuthService
from .exceptions import BlendAPIError, BlendAuthenticationError
from .menu import BlendMenuService
from .orders import BlendOrderService


class BlendWebhookService:
    """Import-compatible facade retained for existing tests and callers."""

    def __init__(self, env):
        self.env = env

    def handle(self, payload, authorization, request_headers=None):
        return self.env["bs.delivery.blend.webhook.service"].handle_legacy(
            payload,
            authorization,
            request_headers=request_headers,
        )


class BlendWebhookServiceModel(models.AbstractModel):
    _name = "bs.delivery.blend.webhook.service"
    _description = "Blend Webhook Compatibility Service"

    STATUS_EVENTS = frozenset({"order_updated", "order.updated"})

    def handle_legacy(self, payload, authorization, request_headers=None):
        if not isinstance(payload, dict):
            raise BlendAPIError("The request body must be a JSON object.")
        event = str(payload.get("event") or "").strip()
        if not event:
            raise BlendAPIError("event is required.")
        stores = self._authorized_stores(self._resolve_stores(payload), authorization)
        results = []
        duplicate_count = 0
        for store in stores:
            result, duplicate = self._handle_for_store(
                store,
                event,
                payload,
                request_headers=request_headers,
            )
            results.append(result)
            duplicate_count += int(duplicate)
        return {
            "status": "success",
            "event": event,
            "processed_branches": len(stores) - duplicate_count,
            "duplicate_branches": duplicate_count,
            "result": results[0] if len(results) == 1 else results,
        }

    def dispatch_for_connection(self, connection, payload, *, webhook_event=None):
        self = self.with_env(connection._blend_execution_environment())
        connection = connection.with_env(self.env)
        if webhook_event:
            webhook_event = webhook_event.with_env(self.env)
        event = str(payload.get("event") or "").strip()
        stores = self._resolve_stores(payload, connection=connection)
        results = []
        bindings = self.env["bs.delivery.order.binding"]
        for store in stores:
            result, binding = self._dispatch(
                store,
                event,
                payload,
                webhook_event=webhook_event,
            )
            results.append(result)
            bindings |= binding
        return (results[0] if len(results) == 1 else results), bindings

    @staticmethod
    def _branch_id(payload):
        nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        return str(payload.get("branch_id") or nested.get("branch_id") or "").strip()

    def _resolve_stores(self, payload, *, connection=None):
        branch_id = self._branch_id(payload)
        brand_id = str(payload.get("brand_id") or "").strip()
        if not branch_id and not brand_id:
            raise BlendAPIError("branch_id or brand_id is required.")
        domain = [
            ("connection_id.provider_id.code", "=", "blend"),
            ("connection_id.active", "=", True),
            ("connection_id.state", "=", "connected"),
            ("active", "=", True),
        ]
        if connection:
            domain.append(("connection_id", "=", connection.id))
        if branch_id:
            domain.append(("remote_store_id", "=", branch_id))
        else:
            domain.extend(
                [
                    "|",
                    ("remote_vendor_id", "=", brand_id),
                    ("connection_id.blend_brand_id", "=", brand_id),
                ]
            )
        stores = self.env["bs.delivery.store.binding"].sudo().search(domain)
        if not stores:
            raise BlendAPIError("No enabled Blend POS branch matches this event.", 404)
        if branch_id and len(stores) > 1:
            raise BlendAPIError("Blend branch mapping is ambiguous.", 409)
        return stores

    @staticmethod
    def _bearer_token(authorization):
        prefix = "Bearer "
        if not authorization or not authorization.startswith(prefix):
            raise BlendAuthenticationError()
        token = authorization[len(prefix) :].strip()
        if not token:
            raise BlendAuthenticationError()
        return token

    def _authorized_stores(self, stores, authorization):
        return BlendAuthService(self.env).authorized_stores(stores, authorization)

    def _handle_for_store(self, store, event, payload, *, request_headers=None):
        try:
            self = self.with_env(store.connection_id._blend_execution_environment())
        except DeliveryAuthenticationError as error:
            raise BlendAPIError(str(error), 403) from error
        store = store.with_env(self.env)
        connection = store.connection_id.with_company(store.company_id)
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        payload_digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        event_id = str(payload.get("event_id") or "").strip()
        idempotency_key = hashlib.sha256(
            ("blend-legacy:%s:%s:%s:%s" % (
                connection.id, store.id, event, event_id or payload_digest
            )).encode()
        ).hexdigest()
        Event = self.env["bs.delivery.webhook.event"].sudo()
        values = {
            "connection_id": connection.id,
            "store_binding_id": store.id,
            "signature_verified": True,
            "event_id": event_id or False,
            "idempotency_key": idempotency_key,
            "event_type": event,
            # Core status updates resolve their canonical order after auth.
            # The wire order_id can be a Blend API UUID, not Core's external ID.
            "external_id": False if event in self.STATUS_EVENTS else (
                self._external_reference(event, payload) or False
            ),
            "correlation_id": str(uuid.uuid4()),
            "payload_digest": payload_digest,
            "payload_size": len(serialized.encode("utf-8")),
            "raw_payload": serialized,
            "payload_preview": serialize_masked_payload(serialized),
            "request_headers": serialize_masked_payload(request_headers or {}),
            "max_attempts": connection.webhook_max_attempts,
            "state": "queued",
            "next_attempt_at": fields.Datetime.now(),
        }
        try:
            webhook_event, created = Event._get_or_create_event(connection, values)
        except DeliveryWebhookConflictError as error:
            raise BlendAPIError(str(error), status_code=409) from error
        if not created and webhook_event.state == "done":
            result_log = self.env["bs.delivery.sync.log"].sudo().search(
                [
                    ("webhook_event_id", "=", webhook_event.id),
                    ("operation", "=", "blend_legacy:%s" % event),
                    ("status", "=", "success"),
                ],
                order="id desc",
                limit=1,
            )
            return self._stored_result(result_log.response_payload), True

        started = time.monotonic()
        webhook_event.write(
            {
                "state": "processing",
                "attempt_count": webhook_event.attempt_count + 1,
                "last_attempt_at": fields.Datetime.now(),
                "next_attempt_at": False,
                "last_error": False,
                "error_type": False,
            }
        )
        log = self.env["bs.delivery.sync.log"].start_attempt(
            connection,
            direction="incoming",
            operation="blend_legacy:%s" % event,
            webhook_event=webhook_event,
            external_id=self._external_reference(event, payload),
            request_url="/api/blend/webhook",
            request_method="POST",
            request_payload=payload,
            attempt_number=webhook_event.attempt_count,
            correlation_id=webhook_event.correlation_id,
        )
        try:
            with self.env.cr.savepoint():
                result, binding = self._dispatch(
                    store,
                    event,
                    payload,
                    webhook_event=webhook_event,
                )
        except BlendAPIError as error:
            duration = (time.monotonic() - started) * 1000
            webhook_event.write(
                {
                    "state": "failed",
                    "processed_at": fields.Datetime.now(),
                    "error_type": (
                        "authentication_error" if error.status_code in (401, 403) else "business_error"
                    ),
                    "last_error": error.message,
                }
            )
            log.mark_failed(
                error.message,
                response_payload={"status": "failed", "message": error.message},
                http_status=error.status_code,
                duration_ms=duration,
            )
            raise
        except Exception as error:
            duration = (time.monotonic() - started) * 1000
            webhook_event.write(
                {
                    "state": "failed",
                    "processed_at": fields.Datetime.now(),
                    "error_type": "unexpected_error",
                    "last_error": str(error)[:4000],
                }
            )
            log.mark_failed(
                str(error),
                response_payload={"status": "failed", "message": str(error)},
                http_status=500,
                duration_ms=duration,
            )
            raise BlendAPIError("Failed to process Blend event.", 500) from error

        completed = fields.Datetime.now()
        update = {
            "state": "done",
            "processed_at": completed,
            "last_error": False,
            "error_type": False,
        }
        if binding:
            update["binding_id"] = binding.id
            log.write({"binding_id": binding.id})
        webhook_event.write(update)
        connection.sudo().write({"last_sync_date": completed})
        log.mark_success(
            response_payload=result or {},
            http_status=200,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        return result or {}, False

    def _dispatch(self, store, event, body, *, webhook_event=None):
        connection = store.connection_id.with_company(store.company_id)

        menu_service = BlendMenuService(self.env, connection, store)
        nested = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        if event == "menu_sync":
            return menu_service.sync_menu(body), self.env["bs.delivery.order.binding"]
        if event == "item_availability":
            return (
                menu_service.set_item_availability(
                    nested.get("item_id"), nested.get("is_available")
                ),
                self.env["bs.delivery.order.binding"],
            )
        if event == "modifier_availability":
            return (
                menu_service.set_modifier_availability(
                    nested.get("modifier_id"), nested.get("is_available")
                ),
                self.env["bs.delivery.order.binding"],
            )
        if event == "branch_availability":
            return (
                menu_service.set_branch_availability(nested.get("is_available")),
                self.env["bs.delivery.order.binding"],
            )
        if event == "order_updated":
            return BlendOrderService(self.env, connection, store).apply_inbound_status(
                nested.get("order_id"),
                nested.get("status"),
                webhook_event=webhook_event,
                event_time=body.get("event_time"),
            )
        if event == "menu.updated":
            if not connection.blend_menu_url:
                raise BlendAPIError(
                    "Blend Get Menu URL is not configured for this branch.", 422
                )
            result = connection._get_adapter().sync_menu(
                connection,
                store_binding=store,
                tolerate_failure=True,
            )
            return (
                {"menu_pull_log_id": result.get("log_id")},
                self.env["bs.delivery.order.binding"],
            )
        if event == "item.updated":
            return (
                menu_service.set_item_availability(
                    body.get("product_id"), body.get("is_stock"), by_sku=True
                ),
                self.env["bs.delivery.order.binding"],
            )
        if event == "store.updated":
            return (
                menu_service.set_branch_availability(body.get("status")),
                self.env["bs.delivery.order.binding"],
            )
        if event == "order.updated":
            return BlendOrderService(self.env, connection, store).apply_inbound_status(
                body.get("order_id"),
                body.get("status"),
                webhook_event=webhook_event,
                event_time=body.get("event_time"),
            )
        raise BlendAPIError("Unsupported Blend event '%s'." % event)

    @staticmethod
    def _external_reference(event, payload):
        nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        if "order" in event:
            return str(
                nested.get("order_id")
                or payload.get("order_id")
                or nested.get("id")
                or ""
            )
        return str(
            nested.get("item_id")
            or nested.get("modifier_id")
            or payload.get("product_id")
            or payload.get("branch_id")
            or ""
        )

    @staticmethod
    def _stored_result(value):
        if not value:
            return {}
        try:
            result = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return result if isinstance(result, dict) else {}
