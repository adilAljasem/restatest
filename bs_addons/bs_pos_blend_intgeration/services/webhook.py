import logging
import time
from datetime import datetime

from odoo import fields

from .auth import BlendAuthService
from .exceptions import BlendAPIError
from .menu import BlendMenuService
from .orders import BlendOrderService


_logger = logging.getLogger(__name__)


class BlendWebhookService:
    def __init__(self, env):
        self.env = env
        self.log_model = env["blend.integration.log"].sudo()

    @staticmethod
    def _branch_id(payload):
        nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        return str(payload.get("branch_id") or nested.get("branch_id") or "").strip()

    def _resolve_configs(self, payload):
        branch_id = self._branch_id(payload)
        brand_id = str(payload.get("brand_id") or "").strip()
        domain = [("blend_enabled", "=", True)]
        if branch_id:
            domain.append(("blend_branch_id", "=", branch_id))
        elif brand_id:
            domain.append(("blend_brand_id", "=", brand_id))
        else:
            raise BlendAPIError("branch_id or brand_id is required.")
        configs = self.env["pos.config"].sudo().search(domain)
        if not configs:
            raise BlendAPIError("No enabled Blend POS branch matches this event.", 404)
        if branch_id and len(configs) > 1:
            raise BlendAPIError("Blend branch mapping is ambiguous.", 409)
        return configs

    @staticmethod
    def _required(payload, field_name):
        value = payload.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise BlendAPIError("%s is required." % field_name)
        return value

    @classmethod
    def _required_boolean(cls, payload, field_name):
        value = cls._required(payload, field_name)
        if not isinstance(value, bool):
            raise BlendAPIError("%s must be a JSON boolean." % field_name)
        return value

    def _validate_contract(self, event, payload):
        event_time = self._required(payload, "event_time")
        if not isinstance(event_time, str):
            raise BlendAPIError("event_time must be a timestamp string.")
        try:
            datetime.fromisoformat(event_time.strip().replace("Z", "+00:00"))
        except ValueError:
            raise BlendAPIError("event_time must be a valid ISO timestamp.") from None

        nested = payload.get("payload")
        primary_events = {
            "menu_sync",
            "item_availability",
            "modifier_availability",
            "branch_availability",
            "order_updated",
        }
        if event in primary_events:
            if not self._branch_id(payload):
                raise BlendAPIError("branch_id is required.")
            if not isinstance(nested, dict):
                raise BlendAPIError("payload must be an object.")

        if event == "item_availability":
            self._required(nested, "item_id")
            self._required(nested, "is_available")
        elif event == "modifier_availability":
            self._required(nested, "modifier_id")
            self._required(nested, "is_available")
        elif event == "branch_availability":
            self._required(nested, "branch_id")
            self._required(nested, "is_available")
        elif event == "order_updated":
            self._required(nested, "order_id")
            self._required(nested, "status")
        elif event == "menu.updated":
            raise BlendAPIError(
                "menu.updated is outbound-only and must be sent from POS to Blend."
            )
        elif event in ("item.updated", "store.updated", "order.updated"):
            self._required(payload, "brand_id")
            self._required(payload, "branch_id")
            if event == "item.updated":
                self._required(payload, "product_id")
                self._required_boolean(payload, "is_stock")
            elif event == "store.updated":
                self._required_boolean(payload, "status")
            else:
                self._required(payload, "order_id")
                self._required(payload, "status")

    def handle(self, payload, authorization, request_headers=None):
        if not isinstance(payload, dict):
            raise BlendAPIError("The request body must be a JSON object.")
        event = str(payload.get("event") or "").strip()
        if not event:
            raise BlendAPIError("event is required.")
        self._validate_contract(event, payload)
        authorized_configs = BlendAuthService(self.env).authorized_configs(authorization)
        configs = self._resolve_configs(payload).filtered(
            lambda config: config in authorized_configs
        )
        if not configs:
            raise BlendAPIError("No authorized Blend POS branch matches this event.", 404)
        results = []
        duplicate_count = 0
        for config in configs:
            result, duplicate = self._handle_for_config(
                config,
                event,
                payload,
                request_headers=request_headers,
            )
            results.append(result)
            duplicate_count += int(duplicate)
        return {
            "status": "success",
            "event": event,
            "processed_branches": len(configs) - duplicate_count,
            "duplicate_branches": duplicate_count,
            "result": results[0] if len(results) == 1 else results,
        }

    def _handle_for_config(self, config, event, payload, request_headers=None):
        key = self.log_model.make_idempotency_key(
            payload,
            prefix="inbound:%s:%s" % (config.id, event),
        )
        self.log_model.lock_idempotency_key(key)
        log = self.log_model.search(
            [
                ("company_id", "=", config.company_id.id),
                ("direction", "=", "inbound"),
                ("idempotency_key", "=", key),
            ],
            limit=1,
        )
        if log and log.state == "success":
            return log.response_payload or {}, True
        if not log:
            event_time = False
            try:
                event_time = fields.Datetime.to_datetime(payload.get("event_time"))
            except (TypeError, ValueError):
                event_time = False
            log = self.log_model.create(
                {
                    "name": "%s - %s" % (event, key[-12:]),
                    "direction": "inbound",
                    "event": event,
                    "event_time": event_time,
                    "state": "received",
                    "company_id": config.company_id.id,
                    "config_id": config.id,
                    "external_reference": self._external_reference(event, payload),
                    "idempotency_key": key,
                    "request_payload": payload,
                    "request_headers": request_headers or {},
                }
            )
        started_at = fields.Datetime.now()
        started_clock = time.perf_counter()
        log.write(
            {
                "state": "processing",
                "started_at": started_at,
                "last_attempt_at": started_at,
                "attempt_count": log.attempt_count + 1,
                "error_message": False,
            }
        )
        try:
            with self.env.cr.savepoint():
                result = self._dispatch(config, event, payload)
            completed = fields.Datetime.now()
            log.write(
                {
                    "state": "success",
                    "response_payload": result or {},
                    "http_status": 200,
                    "completed_at": completed,
                    "duration_ms": (time.perf_counter() - started_clock) * 1000,
                }
            )
            return result or {}, False
        except BlendAPIError as error:
            self._mark_failed(log, error, started_clock)
            raise
        except Exception as error:
            _logger.exception("Unexpected Blend webhook failure event=%s", event)
            self._mark_failed(log, error, started_clock)
            raise BlendAPIError("Failed to process Blend event.", 500) from error

    @staticmethod
    def _external_reference(event, payload):
        nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        if "order" in event:
            return str(nested.get("order_id") or payload.get("order_id") or nested.get("id") or "")
        return str(
            nested.get("item_id")
            or nested.get("modifier_id")
            or payload.get("product_id")
            or payload.get("branch_id")
            or ""
        )

    def _mark_failed(self, log, error, started_clock):
        log.write(
            {
                "state": "failed",
                "http_status": getattr(error, "status_code", 500),
                "completed_at": fields.Datetime.now(),
                "duration_ms": (time.perf_counter() - started_clock) * 1000,
                "error_message": str(error)[:4000],
                "response_payload": {
                    "status": "failed",
                    "message": getattr(error, "message", str(error)),
                },
            }
        )

    def _dispatch(self, config, event, body):
        menu_service = BlendMenuService(self.env, config)
        nested = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        if event == "menu_sync":
            return menu_service.sync_menu(body)
        if event == "item_availability":
            return menu_service.set_item_availability(
                nested.get("item_id"), nested.get("is_available")
            )
        if event == "modifier_availability":
            return menu_service.set_modifier_availability(
                nested.get("modifier_id"), nested.get("is_available")
            )
        if event == "branch_availability":
            return menu_service.set_branch_availability(nested.get("is_available"))
        if event == "order_updated":
            return BlendOrderService(self.env, config).apply_inbound_status(
                nested.get("order_id"), nested.get("status")
            )
        if event == "item.updated":
            return menu_service.set_item_availability(
                body.get("product_id"), body.get("is_stock"), by_sku=True
            )
        if event == "store.updated":
            return menu_service.set_branch_availability(body.get("status"))
        if event == "order.updated":
            return BlendOrderService(self.env, config).apply_inbound_status(
                body.get("order_id"), body.get("status")
            )
        raise BlendAPIError("Unsupported Blend event '%s'." % event)

