import hashlib
import json
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BlendIntegrationLog(models.Model):
    _name = "blend.integration.log"
    _description = "Blend Integration Log and Retry Queue"
    _order = "create_date desc, id desc"
    _blend_processing_timeout_minutes = 10

    name = fields.Char(required=True, readonly=True, index=True)
    direction = fields.Selection(
        [("inbound", "Inbound"), ("outbound", "Outbound")],
        required=True,
        readonly=True,
        index=True,
    )
    event = fields.Char(required=True, readonly=True, index=True)
    event_time = fields.Datetime(readonly=True, index=True)
    state = fields.Selection(
        [
            ("received", "Received"),
            ("processing", "Processing"),
            ("pending", "Pending"),
            ("retry", "Retry Scheduled"),
            ("success", "Success"),
            ("failed", "Failed"),
            ("duplicate", "Duplicate"),
            ("superseded", "Superseded"),
        ],
        required=True,
        default="received",
        readonly=True,
        index=True,
    )
    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True)
    config_id = fields.Many2one("pos.config", required=True, readonly=True, index=True)
    order_id = fields.Many2one("pos.order", readonly=True, index=True, ondelete="set null")
    external_reference = fields.Char(readonly=True, index=True)
    idempotency_key = fields.Char(required=True, readonly=True, index=True)
    endpoint_url = fields.Char(readonly=True)
    http_method = fields.Selection(
        [("get", "GET"), ("post", "POST")],
        default="post",
        required=True,
        readonly=True,
    )
    request_payload = fields.Json(readonly=True)
    stock_check = fields.Json(
        readonly=True,
        help="Internal pre-change product availability. Rechecked by the committed queue worker; never sent to Blend.",
    )
    menu_scope = fields.Json(
        string="Shared Menu Scope", readonly=True,
        help="Affected POS configuration IDs and a destination fingerprint. Internal audit data; never sent to Blend.",
    )
    request_headers = fields.Json(readonly=True)
    response_payload = fields.Json(readonly=True)
    http_status = fields.Integer(readonly=True)
    attempt_count = fields.Integer(default=0, readonly=True)
    next_retry_at = fields.Datetime(readonly=True, index=True)
    last_attempt_at = fields.Datetime(readonly=True)
    started_at = fields.Datetime(readonly=True)
    completed_at = fields.Datetime(readonly=True)
    duration_ms = fields.Float(readonly=True, digits=(16, 3))
    error_message = fields.Text(readonly=True)

    _blend_log_idempotency_unique = models.Constraint(
        "unique (company_id, direction, idempotency_key)",
        "This Blend integration operation has already been recorded.",
    )

    @api.model
    def make_idempotency_key(self, payload, prefix="blend"):
        serialized = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return "%s:%s" % (prefix, digest)

    @api.model
    def lock_idempotency_key(self, key):
        """Serialize identical requests until the current transaction ends."""
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [str(key)],
        )

    @api.model
    def _event_endpoint(self, config, event):
        if event in ("menu.updated", "item.updated", "store.updated", "order.updated") and config.blend_outbound_webhook_url:
            return config.blend_outbound_webhook_url, "post"
        if event == "order_created":
            return config.blend_order_url, "post"
        if event in ("order_updated", "order.updated"):
            return config.blend_status_url, "post"
        if event == "menu.updated":
            return config.blend_event_url, "post"
        return False, "post"

    def _order_status(self):
        """Read both existing wire contracts without rewriting queued payloads."""
        self.ensure_one()
        payload = self.request_payload or {}
        if not isinstance(payload, dict):
            return ""
        if self.event == "order_updated":
            payload = payload.get("payload") or {}
        elif self.event != "order.updated":
            return ""
        if not isinstance(payload, dict):
            return ""
        status = str(payload.get("status") or "").strip().lower()
        return {"canceled": "cancelled", "timeout": "time_out"}.get(status, status)

    @api.model
    def enqueue(
        self,
        config,
        event,
        payload,
        *,
        order=None,
        idempotency_key=None,
        external_reference=None,
        endpoint_url=None,
        http_method=None,
        stock_check=None,
        menu_scope=None,
    ):
        config.ensure_one()
        if event == "order_created" and not config._blend_legacy_order_creation_active():
            return self.browse()
        key = idempotency_key or self.make_idempotency_key(
            payload,
            prefix="%s:%s" % (event, config.id),
        )
        self.lock_idempotency_key(key)
        existing = self.sudo().search(
            [
                ("company_id", "=", config.company_id.id),
                ("direction", "=", "outbound"),
                ("idempotency_key", "=", key),
            ],
            limit=1,
        )
        if existing:
            if menu_scope:
                old_scope = existing.menu_scope or {}
                if (existing.event != "menu.updated" or existing.stock_check
                        or old_scope.get("destination_key") != menu_scope["destination_key"]):
                    raise UserError(_("Conflicting shared menu notification scope."))
                ids = sorted(set(old_scope["config_ids"]) | set(menu_scope["config_ids"]))
                if ids != old_scope["config_ids"]:
                    # Nested hooks may discover more affected branches, but
                    # cannot widen the audit scope of an already attempted job.
                    existing.lock_for_update(allow_referencing=True)
                    existing.invalidate_recordset()
                    if existing.state != "pending" or existing.last_attempt_at:
                        raise UserError(_("Cannot extend an already attempted menu notification. Save a new menu change instead."))
                    existing.menu_scope = {**old_scope, "config_ids": ids}
            return existing
        resolved_url, resolved_method = self._event_endpoint(config, event)
        log = self.sudo().create(
            {
                "name": "%s - %s" % (event, external_reference or key[-12:]),
                "direction": "outbound",
                "event": event,
                "event_time": fields.Datetime.now(),
                "state": "pending",
                "company_id": config.company_id.id,
                "config_id": config.id,
                "order_id": order.id if order else False,
                "external_reference": external_reference,
                "idempotency_key": key,
                "endpoint_url": endpoint_url or resolved_url,
                "http_method": (http_method or resolved_method).lower(),
                "request_payload": payload or {},
                "stock_check": stock_check or False,
                "menu_scope": menu_scope or False,
            }
        )
        log._trigger_queue()
        return log

    def _trigger_queue(self):
        cron = self.env.ref(
            "bs_pos_blend_intgeration.ir_cron_blend_outbound_queue",
            raise_if_not_found=False,
        )
        if cron and cron.active:
            cron._trigger()

    def action_retry(self):
        self.check_access("write")
        retryable = self.filtered(lambda record: record.direction == "outbound")
        if not retryable:
            raise UserError(_("Only outbound operations can be retried."))
        retryable.lock_for_update()
        retryable.invalidate_recordset()
        if any(log.state not in ("failed", "retry", "pending") for log in retryable):
            raise UserError(_("Only waiting or failed outbound operations can be retried."))
        from ..services.exceptions import BlendOutboundError
        from ..services.menu_notifications import BlendMenuNotificationService

        menu_updates = {}
        for log in retryable.filtered("menu_scope"):
            try:
                menu_updates[log.id] = BlendMenuNotificationService(self.env).retry_values(log)
            except BlendOutboundError as error:
                raise UserError(str(error)) from error
        for log in retryable:
            if log.id in menu_updates:
                log.write(menu_updates[log.id])
            elif log.event in ("menu.updated", "item.updated", "store.updated", "order.updated") and log.config_id.blend_outbound_webhook_url:
                log.write({"endpoint_url": log.config_id.blend_outbound_webhook_url, "http_method": "post"})
        retryable.write(
            {
                "state": "pending",
                "attempt_count": 0,
                "next_retry_at": False,
                "completed_at": False,
                "error_message": False,
            }
        )
        retryable._trigger_queue()
        return True

    def action_process_now(self):
        self.check_access("write")
        for log in self.filtered(lambda record: record.direction == "outbound"):
            log._process_outbound()
        return True

    def _process_outbound(self):
        self.ensure_one()
        from ..services.outbound import BlendOutboundService

        return BlendOutboundService(self.env).send(self)

    @api.model
    def _cron_process_outbound(self, batch_size=50):
        now = fields.Datetime.now()
        stuck_before = now - timedelta(minutes=self._blend_processing_timeout_minutes)
        stuck_logs = self.sudo().search(
            [
                ("direction", "=", "outbound"),
                ("state", "=", "processing"),
                ("last_attempt_at", "!=", False),
                ("last_attempt_at", "<=", stuck_before),
            ]
        ).try_lock_for_update(allow_referencing=True)
        stuck_logs.invalidate_recordset()
        stuck_logs = stuck_logs.filtered(
            lambda log: log.state == "processing" and log.last_attempt_at
            and log.last_attempt_at <= stuck_before
        )
        if stuck_logs:
            stuck_logs.write(
                {
                    "state": "retry",
                    "next_retry_at": False,
                    "error_message": "Recovered after an interrupted outbound attempt.",
                }
            )
        logs = self.sudo().search(
            [
                ("direction", "=", "outbound"),
                ("state", "in", ("pending", "retry")),
                "|",
                ("next_retry_at", "=", False),
                ("next_retry_at", "<=", now),
            ],
            order="next_retry_at, id",
            limit=batch_size,
        ).try_lock_for_update()
        for log in logs:
            log._process_outbound()
        return len(logs)

