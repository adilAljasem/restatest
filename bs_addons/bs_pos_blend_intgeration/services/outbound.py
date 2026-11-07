import logging
import time
from datetime import timedelta
from urllib.parse import urlparse

import requests
from psycopg2 import Error as DatabaseError

from odoo import fields

from .exceptions import BlendAPIError, BlendOutboundError


_logger = logging.getLogger(__name__)


class BlendOutboundService:
    RETRY_MINUTES = (1, 5, 15, 60, 360)
    TERMINAL_STATUSES = {"cancelled", "rejected", "time_out", "delivered"}
    # Cooking and driver assignment can overlap; do not invent a total ordering
    # between them. These are only unequivocally later local states.
    LATER_STATUSES = {
        "pending": {"accepted", "preparing", "prepared", "assigned", "at_pick_up", "on_the_way"},
        "accepted": {"preparing", "prepared", "assigned", "at_pick_up", "on_the_way"},
        "preparing": {"prepared", "on_the_way"},
        "prepared": {"on_the_way"},
        "assigned": {"at_pick_up", "on_the_way"},
        "at_pick_up": {"on_the_way"},
        "on_the_way": set(),
    }

    def __init__(self, env):
        self.env = env

    def _headers(self, config):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if config.blend_outbound_token:
            headers["Authorization"] = "Bearer %s" % config.blend_outbound_token
        return headers

    def _validate_url(self, url):
        parsed = urlparse(str(url or ""))
        allow_insecure = self.env["ir.config_parameter"].sudo().get_param(
            "blend.allow_insecure_urls"
        ) == "1"
        if not parsed.netloc or (parsed.scheme != "https" and not allow_insecure):
            raise BlendOutboundError("Blend endpoint must be a valid HTTPS URL.")

    def _request(self, log, headers):
        timeout = min(max(log.config_id.blend_timeout, 1), 120)
        if log.http_method == "get":
            return requests.get(log.endpoint_url, headers=headers, timeout=timeout)
        return requests.post(
            log.endpoint_url,
            json=log.request_payload,
            headers=headers,
            timeout=timeout,
        )

    @staticmethod
    def _response_payload(response):
        try:
            value = response.json()
            return value if isinstance(value, (dict, list)) else {"data": value}
        except ValueError:
            return {"raw": (response.text or "")[:4000]}

    def _rebuild_order_payload(self, log):
        if log.event != "order_created" or not log.request_payload.get("_build_required"):
            return
        if not log.order_id:
            raise BlendOutboundError("The POS order linked to this queue entry no longer exists.")
        payload = log.order_id._blend_build_order_created_payload()
        log.request_payload = payload
        log.order_id.blend_payload = payload

    def _process_success_payload(self, log, response_payload):
        # HTTP delivery is not application acceptance when Blend explicitly
        # rejects it. Do not require new response fields without a confirmed
        # contract: existing 2xx/empty acknowledgements remain compatible.
        if isinstance(response_payload, dict) and response_payload.get("success") is False:
            raise BlendOutboundError("Blend returned success=false.")
        if isinstance(response_payload, dict) and response_payload.get("status") == "failed":
            raise BlendOutboundError("Blend returned status=failed.")
        if log.event == "order_created":
            if not isinstance(response_payload, dict) or response_payload.get("status") != "success":
                raise BlendOutboundError("Blend did not confirm order_created with status=success.")
            blend_order_id = str(response_payload.get("order_id") or "").strip()
            if not blend_order_id:
                raise BlendOutboundError("Blend success response is missing order_id.")
            if log.order_id:
                log.order_id.blend_order_id = blend_order_id
        elif log.event in ("order_updated", "order.updated"):
            if isinstance(response_payload, dict) and response_payload.get("status") not in (None, "success"):
                raise BlendOutboundError("Blend did not accept the order status update.")

    def send(self, log):
        log.ensure_one()
        log.check_access("write")
        log.flush_recordset()
        # The same guard applies to cron, Process Now, and internal callers.
        # SKIP LOCKED avoids competing with a worker already doing HTTP.
        if not log.try_lock_for_update(allow_referencing=True):
            return False
        log.invalidate_recordset()
        if log.direction != "outbound" or log.state not in ("pending", "retry"):
            return False
        if log.event == "order_created" and not log.config_id._blend_legacy_order_creation_active():
            # Retain old jobs and their payloads for an explicit opt-in/retry,
            # but never call Blend or schedule automatic retries while off.
            if log.state != "success":
                log.write({
                    "state": "failed", "next_retry_at": False,
                    "completed_at": fields.Datetime.now(),
                    "error_message": "Legacy order creation is disabled for this POS. Enable Legacy Order Creation, then use Reset and Retry if this event is still required.",
                })
            return False
        started_clock = time.perf_counter()
        stock_state, stock_values = None, {}
        try:
            self._validate_event_destination(log)
            menu_configs = log.config_id
            if log.menu_scope:
                from .menu_notifications import BlendMenuNotificationService

                menu_configs = BlendMenuNotificationService(self.env).delivery_configs(log)
            if log.event in ("order.updated", "order_updated"):
                order = log.order_id
                if order:
                    order.flush_recordset()
                    if not order.try_lock_for_update(allow_referencing=True):
                        return False
                    # Serialize sending with status updates and other messages
                    # for this order. A changed snapshot must fail before HTTP.
                    order.invalidate_recordset([
                        "state", "blend_status", "blend_accepted_delivery_log_id", "blend_status_sync_revision",
                        "blend_partner_reference",
                    ])
                if self._supersede_order_status(log, order):
                    return False
            if log.event == "menu.updated" and log.request_payload.get("entity", {}).get("type") == "product":
                from .pos_api import BlendPOSAPIService

                api = BlendPOSAPIService(self.env, menu_configs)
                products = self.env["product.template"].browse()
                for config in menu_configs:
                    products |= api._products_for_config(config)
                api._product_identity_map(products)
            if log.stock_check:
                from .stock_availability import BlendStockAvailabilityService

                stock_state, stock_values, changed = BlendStockAvailabilityService(self.env).prepare_delivery(log)
                if not stock_state or (not changed and not log.last_attempt_at):
                    if stock_state:
                        stock_state.availability = stock_values
                    log.write({
                        "state": "success",
                        "completed_at": fields.Datetime.now(),
                        "next_retry_at": False,
                        "response_payload": {"skipped": True, "reason": "Availability unchanged or integration disabled."},
                    })
                    return True
        except (BlendAPIError, BlendOutboundError) as error:
            log.write({"attempt_count": log.attempt_count + 1, "last_attempt_at": fields.Datetime.now()})
            return self._schedule_failure(log, error, started_clock)
        # Once attempted, always reconcile: a timeout may mean Blend received
        # it. last_attempt_at also survives a manual reset of attempt_count.
        started = fields.Datetime.now()
        started_clock = time.perf_counter()
        attempt = log.attempt_count + 1
        sanitized_headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if log.config_id.blend_outbound_token:
            sanitized_headers["Authorization"] = "Bearer ***"
        log.write(
            {
                "state": "processing",
                "started_at": started,
                "last_attempt_at": started,
                "attempt_count": attempt,
                "request_headers": sanitized_headers,
                "error_message": False,
                "http_status": False,
                "response_payload": False,
                "completed_at": False,
            }
        )
        try:
            self._rebuild_order_payload(log)
            self._validate_url(log.endpoint_url)
            response = self._request(log, self._headers(log.config_id))
            response_payload = self._response_payload(response)
            log.write(
                {
                    "http_status": response.status_code,
                    "response_payload": response_payload,
                }
            )
            if not 200 <= response.status_code < 300:
                raise BlendOutboundError("Blend returned HTTP %s." % response.status_code)
            self._process_success_payload(log, response_payload)
            if stock_state:
                stock_state.availability = stock_values
            completed = fields.Datetime.now()
            log.write(
                {
                    "state": "success",
                    "http_status": response.status_code,
                    "response_payload": response_payload,
                    "completed_at": completed,
                    "duration_ms": (time.perf_counter() - started_clock) * 1000,
                    "next_retry_at": False,
                }
            )
            if log.event in ("order.updated", "order_updated") and log.order_id:
                # A row lock alone is not a visibility fence in REPEATABLE READ.
                # Persisting the acknowledgment forces an older competing
                # transaction to restart before it can send a second queue job.
                values = {"blend_status_sync_revision": log.order_id.blend_status_sync_revision + 1}
                if log.event == "order.updated" and log._order_status() == "accepted":
                    values["blend_accepted_delivery_log_id"] = log.id
                log.order_id.write(values)
                log.order_id.flush_recordset(["blend_status_sync_revision"])
            return True
        except DatabaseError:
            # Serialization/deadlock failures must retry the entire transaction.
            raise
        except Exception as error:
            _logger.warning(
                "Blend outbound event failed event=%s log=%s attempt=%s error=%s",
                log.event,
                log.id,
                attempt,
                error,
            )
            return self._schedule_failure(log, error, started_clock)

    def _supersede_order_status(self, log, order):
        """Latest intent wins; a stale retry must never undo a newer status.

        Run under the order lock for both wire schemas, including pre-upgrade
        jobs. Keep distinct transitions distinct even if a later status repeats.
        """
        status = log._order_status()
        if status not in self.TERMINAL_STATUSES | self.LATER_STATUSES.keys():
            raise BlendOutboundError("Queued order status is missing or unsupported.")
        if not order:
            reason = "The order linked to this status notification no longer exists."
        elif order.config_id != log.config_id or order.company_id != log.company_id:
            raise BlendOutboundError("Queued status belongs to a different order branch/company.")
        elif not order.blend_partner_reference:
            reason = "Status notifications are only sent for orders received from Blend."
        elif ((status == "accepted" and order._blend_acceptance_obsolete())
              or (order.state == "cancel" and status not in {"cancelled", "rejected", "time_out"})
              or (order.blend_status in self.TERMINAL_STATUSES and order.blend_status != status)
              or order.blend_status in self.LATER_STATUSES.get(status, set())):
            reason = "%s is obsolete: the order is now %s (POS state: %s)." % (
                status,
                order.blend_status or "unset", order.state,
            )
        elif status == "accepted" and order.blend_accepted_delivery_log_id:
            reason = "An ACCEPTED notification was already delivered for this order."
        else:
            related = self.env["blend.integration.log"].search([
                ("id", "!=", log.id), ("order_id", "=", order.id),
                ("config_id", "=", log.config_id.id), ("direction", "=", "outbound"),
                ("company_id", "=", log.company_id.id),
                ("event", "in", ["order.updated", "order_updated"]),
            ])
            different = related.filtered(
                lambda other: other._order_status() in self.TERMINAL_STATUSES | self.LATER_STATUSES.keys()
                and other._order_status() != status
            )
            # Include superseded jobs in the transition boundary: A -> B -> A
            # is not the same intent as repeated callbacks A -> A.
            boundary = max(different.ids, default=0)
            delivered = related.filtered(
                lambda other: other.state == "success"
                and other._order_status() == status and other.id > boundary
            )
            if boundary > log.id:
                reason = "A newer order status has superseded this notification."
            elif delivered:
                reason = "This order status transition was already delivered."
            else:
                return False
        # Preserve the request, previous response, HTTP status and attempts for
        # audit. Superseded is terminal, not a successful delivery or a retry.
        log.write({
            "state": "superseded", "completed_at": fields.Datetime.now(),
            "next_retry_at": False, "error_message": reason,
        })
        return True

    def _validate_event_destination(self, log):
        if log.event not in ("menu.updated", "item.updated", "store.updated", "order.updated"):
            return
        current = log.config_id.blend_outbound_webhook_url
        if current and log.endpoint_url != current:
            raise BlendOutboundError("Webhook URL changed. Review this event, then use Reset and Retry to adopt the current URL.")
        if log.event in ("item.updated", "store.updated") and not current:
            raise BlendOutboundError("Configure a unified Blend Outbound Webhook URL for availability events.")
        if current:
            payload = log.request_payload
            if payload.get("brand_id") != log.config_id.blend_brand_id:
                raise BlendOutboundError("Queued event brand differs from the current branch configuration.")
            if log.event != "menu.updated" and payload.get("branch_id") != log.config_id.blend_branch_id:
                raise BlendOutboundError("Queued event branch differs from the current branch configuration.")

    def _schedule_failure(self, log, error, started_clock):
        maximum = min(max(log.config_id.blend_retry_max, 1), 10)
        state = "failed" if log.attempt_count >= maximum else "retry"
        delay_index = min(max(log.attempt_count - 1, 0), len(self.RETRY_MINUTES) - 1)
        next_retry = (
            fields.Datetime.now() + timedelta(minutes=self.RETRY_MINUTES[delay_index])
            if state == "retry"
            else False
        )
        completed = fields.Datetime.now()
        log.write(
            {
                "state": state,
                "completed_at": completed,
                "duration_ms": (time.perf_counter() - started_clock) * 1000,
                "next_retry_at": next_retry,
                "error_message": str(error)[:4000],
            }
        )
        return False
