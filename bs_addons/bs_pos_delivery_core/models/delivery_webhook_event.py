import hashlib
import ipaddress
import json
import logging
import secrets
import threading
import time
import uuid
from datetime import timedelta

import psycopg2

from odoo import _, api, fields, models
from odoo.modules.registry import Registry
from odoo.exceptions import AccessError, ValidationError

from ..exceptions import (
    DeliveryAuthenticationError,
    DeliveryBusinessError,
    DeliveryIntegrationError,
    DeliveryWebhookConflictError,
    DeliveryWebhookNotFoundError,
    DeliveryWebhookValidationError,
)
from ..tools import mask_sensitive_text, serialize_masked_payload


_logger = logging.getLogger(__name__)


class DeliveryWebhookEvent(models.Model):
    """Durable, idempotent queue entry for one authenticated webhook."""

    _name = "bs.delivery.webhook.event"
    _description = "POS Delivery Webhook Event"
    _rec_name = "event_id"
    _order = "received_at desc, id desc"
    _check_company_auto = True

    connection_id = fields.Many2one(
        "bs.delivery.connection",
        required=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    provider_id = fields.Many2one(
        related="connection_id.provider_id",
        store=True,
        index=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        related="connection_id.company_id",
        store=True,
        index=True,
        readonly=True,
    )
    binding_id = fields.Many2one(
        "bs.delivery.order.binding",
        string="Order Binding",
        index=True,
        ondelete="set null",
        check_company=True,
        copy=False,
    )
    store_binding_id = fields.Many2one(
        "bs.delivery.store.binding",
        string="Remote Store",
        index=True,
        ondelete="set null",
        check_company=True,
        copy=False,
    )
    signature_verified = fields.Boolean(
        string="Signature Verified",
        readonly=True,
        copy=False,
        help="True when the connector authenticated this request before it was queued.",
    )
    client_ip = fields.Char(
        string="Client IP",
        readonly=True,
        copy=False,
        help=(
            "Syntactically validated client IP reported by the HTTP proxy headers. "
            "Only trust it when Odoo is behind a configured trusted proxy."
        ),
    )
    delivery_order_id = fields.Many2one(
        "bs.delivery.order",
        string="Delivery Order",
        compute="_compute_delivery_order_id",
        compute_sudo=True,
    )
    event_id = fields.Char(index=True, copy=False)
    idempotency_key = fields.Char(required=True, index=True, copy=False)
    event_type = fields.Char(required=True, index=True, default="webhook", copy=False)
    external_id = fields.Char(index=True, copy=False)
    correlation_id = fields.Char(
        required=True,
        index=True,
        readonly=True,
        copy=False,
        default=lambda self: str(uuid.uuid4()),
    )
    payload_digest = fields.Char(required=True, index=True, copy=False)
    payload_size = fields.Integer(readonly=True, copy=False)
    raw_payload = fields.Text(
        readonly=True,
        copy=False,
        groups="base.group_system",
        help="Original authenticated request body used by the provider adapter.",
    )
    payload_preview = fields.Text(readonly=True, copy=False)
    request_headers = fields.Text(readonly=True, copy=False)
    received_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        index=True,
        copy=False,
    )
    state = fields.Selection(
        [
            ("queued", "Queued"),
            ("processing", "Processing"),
            ("retry", "Waiting for Retry"),
            ("done", "Done"),
            ("failed", "Failed"),
            ("ignored", "Ignored"),
        ],
        required=True,
        default="queued",
        index=True,
        copy=False,
    )
    attempt_count = fields.Integer(default=0, required=True, readonly=True, copy=False)
    max_attempts = fields.Integer(default=5, required=True, copy=False)
    last_attempt_at = fields.Datetime(readonly=True, copy=False)
    next_attempt_at = fields.Datetime(
        default=fields.Datetime.now,
        index=True,
        copy=False,
    )
    processed_at = fields.Datetime(readonly=True, copy=False)
    processed = fields.Boolean(
        string="Processed",
        compute="_compute_processed",
        help="True when the event reached a terminal successfully handled state.",
    )
    error_type = fields.Selection(
        [
            ("network_error", "Network"),
            ("timeout_error", "Timeout"),
            ("authentication_error", "Authentication"),
            ("validation_error", "Validation"),
            ("provider_server_error", "Provider Server"),
            ("business_error", "Business"),
            ("order_snapshot_conflict", "Order Snapshot Conflict"),
            ("state_conflict", "State Conflict"),
            ("unsupported_operation", "Unsupported Operation"),
            ("integration_error", "Integration"),
            ("unexpected_error", "Unexpected"),
        ],
        readonly=True,
        copy=False,
    )
    last_error = fields.Text(readonly=True, copy=False)
    log_ids = fields.One2many(
        "bs.delivery.sync.log",
        "webhook_event_id",
        string="Logs",
    )
    log_count = fields.Integer(compute="_compute_log_count")

    _idempotency_unique = models.Constraint(
        "UNIQUE(connection_id, idempotency_key)",
        "A webhook event can only be queued once per delivery connection.",
    )
    _attempt_values_valid = models.Constraint(
        "CHECK(attempt_count >= 0 AND max_attempts > 0)",
        "Webhook attempt values are invalid.",
    )
    _payload_values_valid = models.Constraint(
        "CHECK(payload_size >= 0)",
        "Webhook payload size cannot be negative.",
    )

    @api.constrains("connection_id", "binding_id", "store_binding_id")
    def _check_binding_connection(self):
        for event in self:
            if event.binding_id and event.binding_id.connection_id != event.connection_id:
                raise ValidationError(
                    _("The webhook event and order binding must use the same connection.")
                )
            if (
                event.store_binding_id
                and event.store_binding_id.connection_id != event.connection_id
            ):
                raise ValidationError(
                    _("The webhook event store must use the same connection.")
                )

    def _compute_log_count(self):
        counts = {}
        if self.ids:
            counts = {
                event.id: count
                for event, count in self.env["bs.delivery.sync.log"]._read_group(
                    [("webhook_event_id", "in", self.ids)],
                    ["webhook_event_id"],
                    ["__count"],
                )
            }
        for event in self:
            event.log_count = counts.get(event.id, 0)

    def _compute_delivery_order_id(self):
        for event in self:
            event.delivery_order_id = False
        persisted = self.filtered("id")
        if not persisted:
            return
        order_model = self.env["bs.delivery.order"].sudo()
        direct_orders = order_model.search(
            [("webhook_event_id", "in", persisted.ids)], order="id"
        )
        by_event = {}
        for order in direct_orders:
            by_event.setdefault(order.webhook_event_id.id, order)
        missing = persisted.filtered(lambda event: event.id not in by_event)
        by_binding = {}
        binding_ids = missing.mapped("binding_id").ids
        if binding_ids:
            for order in order_model.search(
                [("binding_id", "in", binding_ids)], order="id"
            ):
                by_binding.setdefault(order.binding_id.id, order)
        for event in persisted:
            event.delivery_order_id = by_event.get(event.id) or by_binding.get(
                event.binding_id.id
            )

    @api.depends("state", "processed_at")
    def _compute_processed(self):
        for event in self:
            event.processed = bool(
                event.processed_at and event.state in ("done", "ignored")
            )

    @api.model
    def ingest(self, *, webhook_token, raw_body, headers=None):
        """Authenticate and durably enqueue a request without processing it."""
        token_digest = self.env["bs.delivery.connection"]._webhook_token_digest_value(
            webhook_token
        )
        connection = self.env["bs.delivery.connection"].sudo().search(
            [
                ("webhook_token_digest", "=", token_digest),
                ("active", "=", True),
                ("state", "=", "connected"),
            ],
            limit=1,
        )
        if not connection or not secrets.compare_digest(
            str(connection.webhook_token or ""),
            str(webhook_token or ""),
        ):
            raise DeliveryWebhookNotFoundError(_("Webhook endpoint not found."))

        connection = connection.with_company(connection.company_id)
        if isinstance(raw_body, str):
            raw_body = raw_body.encode()
        raw_body = bytes(raw_body or b"")
        headers = dict(headers or {})
        started_at = time.monotonic()
        correlation_id = str(uuid.uuid4())
        receive_log = self.env["bs.delivery.sync.log"].start_attempt(
            connection,
            direction="incoming",
            operation="webhook_receive",
            request_url="/bs_delivery/webhook/[redacted]",
            request_method="POST",
            request_payload=raw_body,
            correlation_id=correlation_id,
        )

        try:
            verified = connection._dispatch(
                "verify_webhook_signature",
                raw_body=raw_body,
                headers=headers,
            )
            if verified is not True:
                raise DeliveryAuthenticationError(_("Invalid webhook signature."))

            payload = self._decode_payload(raw_body)
            metadata = connection._dispatch(
                "get_webhook_event_metadata",
                payload=payload,
                raw_body=raw_body,
                headers=headers,
            )
            store_binding = self._resolve_ingress_store(
                connection, metadata, payload
            )
            store_binding = self._assert_webhook_receiving_available(
                connection, store_binding, metadata, payload
            )
            values = self._prepare_event_values(
                connection,
                raw_body,
                headers,
                metadata,
                store_binding=store_binding,
                correlation_id=correlation_id,
            )
            event, created = self._get_or_create_event(connection, values)
        except DeliveryIntegrationError as error:
            receive_log.mark_failed(
                str(error),
                duration_ms=(time.monotonic() - started_at) * 1000,
            )
            raise
        except Exception:
            receive_log.mark_failed(
                _("Unexpected webhook ingestion failure."),
                duration_ms=(time.monotonic() - started_at) * 1000,
            )
            _logger.exception(
                "Unexpected webhook ingestion failure for connection %s",
                connection.id,
            )
            raise

        receive_log.write({"webhook_event_id": event.id, "external_id": event.external_id})
        receive_log.mark_success(
            response_payload={"status": "queued" if created else "duplicate"},
            http_status=202 if created else 200,
            duration_ms=(time.monotonic() - started_at) * 1000,
        )
        event._trigger_processing()
        return event, created

    @api.model
    def _decode_payload(self, raw_body):
        try:
            return json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, TypeError, ValueError) as error:
            raise DeliveryWebhookValidationError(
                _("The webhook body must be valid JSON.")
            ) from error

    @api.model
    def _prepare_event_values(
        self,
        connection,
        raw_body,
        headers,
        metadata,
        *,
        store_binding=None,
        correlation_id=None,
    ):
        if not isinstance(metadata, dict):
            raise DeliveryWebhookValidationError(
                _("The provider adapter returned invalid webhook metadata.")
            )

        event_id = str(metadata.get("event_id") or "").strip() or False
        event_type = str(metadata.get("event_type") or "webhook").strip()
        if not event_type:
            raise DeliveryWebhookValidationError(_("Webhook event type cannot be empty."))

        payload_digest = hashlib.sha256(raw_body).hexdigest()
        idempotency_source = metadata.get("idempotency_key") or event_id or payload_digest
        idempotency_key = hashlib.sha256(str(idempotency_source).encode()).hexdigest()
        raw_text = raw_body.decode("utf-8")
        return {
            "connection_id": connection.id,
            "store_binding_id": store_binding.id if store_binding else False,
            "signature_verified": True,
            "client_ip": self._extract_client_ip(headers),
            "event_id": event_id,
            "idempotency_key": idempotency_key,
            "event_type": event_type,
            "external_id": str(metadata.get("external_id") or "").strip() or False,
            "correlation_id": correlation_id or str(uuid.uuid4()),
            "payload_digest": payload_digest,
            "payload_size": len(raw_body),
            "raw_payload": raw_text,
            "payload_preview": serialize_masked_payload(raw_text),
            "request_headers": serialize_masked_payload(headers, max_chars=16_000),
            "max_attempts": connection.webhook_max_attempts,
            "state": "queued",
            "next_attempt_at": fields.Datetime.now(),
        }

    @staticmethod
    def _extract_client_ip(headers):
        """Return one canonical IP without persisting arbitrary header text."""
        normalized = {
            str(key).strip().lower(): str(value or "").strip()
            for key, value in dict(headers or {}).items()
        }
        candidates = []
        forwarded = normalized.get("x-forwarded-for")
        if forwarded:
            candidates.extend(forwarded.split(","))
        for key in ("cf-connecting-ip", "true-client-ip", "x-real-ip"):
            if normalized.get(key):
                candidates.append(normalized[key])
        for candidate in candidates:
            value = candidate.strip().strip('"').strip("'")
            if value.lower().startswith("for="):
                value = value[4:].strip().strip('"').strip("'")
            if value.startswith("[") and "]" in value:
                value = value[1 : value.index("]")]
            try:
                return ipaddress.ip_address(value).compressed
            except ValueError:
                # Proxies occasionally append a port to an IPv4 address.
                host, separator, port = value.rpartition(":")
                if not separator or not port.isdigit():
                    continue
                try:
                    return ipaddress.ip_address(host).compressed
                except ValueError:
                    continue
        return False

    @api.model
    def _resolve_ingress_store(self, connection, metadata, payload=None):
        """Resolve optional adapter metadata to one connection-owned store.

        Adapters should return ``store_binding_id`` or a remote reference in
        their metadata.  A conservative shallow payload fallback covers the
        common ``store_id``/``branch_id`` webhook shapes without treating a
        generic object ``id`` as a store identifier.
        """
        if not isinstance(metadata, dict):
            raise DeliveryWebhookValidationError(
                _("The provider adapter returned invalid webhook metadata.")
            )
        explicit = metadata.get("store_binding_id")
        if explicit:
            explicit_id = explicit.id if getattr(explicit, "_name", False) else explicit
            try:
                explicit_id = int(explicit_id)
            except (TypeError, ValueError) as error:
                raise DeliveryWebhookValidationError(
                    _("The webhook store binding identifier is invalid.")
                ) from error
            store = (
                self.env["bs.delivery.store.binding"]
                .sudo()
                .with_context(active_test=False)
                .browse(explicit_id)
                .exists()
            )
            if not store or store.connection_id != connection:
                raise DeliveryWebhookValidationError(
                    _("The webhook store binding does not belong to this connection.")
                )
            return store

        references = self._ingress_store_references(metadata, payload)
        if not references:
            return self.env["bs.delivery.store.binding"]
        stores = self.env["bs.delivery.store.binding"].sudo().with_context(
            active_test=False
        ).search(
            [
                ("connection_id", "=", connection.id),
                "|",
                "|",
                ("remote_store_id", "in", references),
                ("remote_vendor_id", "in", references),
                ("remote_integration_id", "in", references),
            ]
        )
        if len(stores) > 1:
            raise DeliveryWebhookValidationError(
                _("The webhook store reference matches more than one configured store.")
            )
        return stores

    @api.model
    def _ingress_store_references(self, metadata, payload=None):
        references = set()
        direct_keys = (
            "remote_store_id",
            "store_id",
            "branch_id",
            "vendor_id",
            "integration_id",
        )
        sources = [metadata]
        if isinstance(payload, dict):
            sources.append(payload)
            for key in ("payload", "data", "order", "details"):
                nested = payload.get(key)
                if isinstance(nested, dict):
                    sources.append(nested)
            for key in ("store", "branch", "vendor"):
                nested = payload.get(key)
                if isinstance(nested, dict):
                    sources.append(nested)
                    value = str(nested.get("id") or "").strip()
                    if value:
                        references.add(value)
        for source in sources:
            for key in direct_keys:
                value = str(source.get(key) or "").strip()
                if value:
                    references.add(value)
        return sorted(references)

    @api.model
    def _assert_webhook_receiving_available(
        self, connection, store_binding, metadata, payload
    ):
        """Pauses stop new intake; known orders can still receive callbacks.

        Use the adapter's external order identity, never an event-type label
        or a provider-supplied local POS ID, as evidence of an existing order.
        Keep the original pause guard for callers receiving new orders.
        """
        external_id = str(metadata.get("external_id") or "").strip()
        order = self.env["bs.delivery.order"].sudo().search(
            [
                ("connection_id", "=", connection.id),
                ("company_id", "=", connection.company_id.id),
                ("external_order_id", "=", external_id),
            ],
            limit=1,
        ) if external_id else self.env["bs.delivery.order"]
        if order:
            if store_binding and store_binding != order.store_binding_id:
                raise DeliveryWebhookValidationError(
                    _("The webhook store does not match the existing order's store.")
                )
            if not store_binding and self._ingress_store_references(metadata, payload):
                raise DeliveryWebhookValidationError(
                    _("The webhook store reference does not match a configured store.")
                )
            store_binding = order.store_binding_id
            # Archiving a store disables it; it is not a temporary intake pause.
            if not store_binding or store_binding.active:
                return store_binding
        self._assert_receiving_available(connection, store_binding)
        return store_binding

    @api.model
    def _assert_receiving_available(self, connection, store_binding=None):
        if connection.is_receiving_paused():
            reason = str(connection.pause_reason or "").strip()
            raise DeliveryBusinessError(
                _(
                    "Receiving is paused for %(connection)s.%(reason)s",
                    connection=connection.display_name,
                    reason=" %s" % reason if reason else "",
                ),
                code="receiving_paused",
            )
        if not store_binding:
            return True
        if store_binding.active and not store_binding.is_paused:
            if store_binding.work_time_enabled and not store_binding.is_in_delivery_work_time():
                raise DeliveryBusinessError(
                    _(
                        "Receiving is closed outside operational work hours for %(store)s.",
                        store=store_binding.pos_config_id.display_name or store_binding.remote_store_id,
                    ),
                    code="receiving_outside_work_time",
                )
            return True
        reason = str(store_binding.pause_reason or "").strip()
        if not reason and store_binding.pause_reject_reason_id:
            reason = store_binding.pause_reject_reason_id.display_name
        raise DeliveryBusinessError(
            _(
                "Receiving is paused for remote store %(store)s.%(reason)s",
                store=store_binding.remote_store_id,
                reason=" %s" % reason if reason else "",
            ),
            code="receiving_paused",
        )

    @api.model
    def _get_or_create_event(self, connection, values):
        domain = [
            ("connection_id", "=", connection.id),
            ("idempotency_key", "=", values["idempotency_key"]),
        ]
        event = self.search(domain, limit=1)
        if event:
            self._validate_duplicate_digest(event, values["payload_digest"])
            return event, False
        try:
            with self.env.cr.savepoint():
                event = self.create(values)
            return event, True
        except psycopg2.errors.UniqueViolation:
            event = self.search(domain, limit=1)
            if not event:
                raise
            self._validate_duplicate_digest(event, values["payload_digest"])
            return event, False

    @api.model
    def _validate_duplicate_digest(self, event, payload_digest):
        """Reject an idempotency-key collision instead of losing a mutation.

        Providers may retry the exact same payload safely. Reusing the same
        event/idempotency key for different bytes is a protocol conflict and
        must never be reported as a successful duplicate.
        """
        event.ensure_one()
        if not secrets.compare_digest(
            str(event.payload_digest or ""), str(payload_digest or "")
        ):
            raise DeliveryWebhookConflictError(
                _("The webhook idempotency key was reused with a different payload.")
            )

    def _trigger_processing(self):
        cron = self.env.ref(
            "bs_pos_delivery_core.ir_cron_bs_delivery_process_webhooks",
            raise_if_not_found=False,
        )
        if cron:
            # Safety net only.  A cron trigger can wait up to a minute (or
            # more on Odoo.sh) for a free cron worker, which delayed the
            # order, its acceptance and its kitchen ticket.
            cron.sudo()._trigger()
        self._schedule_inline_processing()

    def _schedule_inline_processing(self):
        """Process the events right after this transaction commits.

        The event is first committed durably, then processed in a fresh
        cursor within the same request, so the order reaches the POS and the
        kitchen printer within seconds instead of waiting for the cron.  The
        row lock (SKIP LOCKED) makes the cron and this path mutually
        exclusive, and any failure leaves the event to the normal retry queue.
        """
        event_ids = self.ids
        if not event_ids or getattr(threading.current_thread(), "testing", False):
            return
        dbname = self.env.cr.dbname
        uid = self.env.uid
        context = dict(self.env.context)

        def process_after_commit():
            try:
                with Registry(dbname).cursor() as cr:
                    env = api.Environment(cr, uid, context)
                    Event = env["bs.delivery.webhook.event"].sudo()
                    for event_id in event_ids:
                        event = Event.browse(event_id).exists().try_lock_for_update(limit=1)
                        if not event or event.state not in ("queued", "retry"):
                            continue
                        if event.next_attempt_at and event.next_attempt_at > fields.Datetime.now():
                            continue
                        with cr.savepoint():
                            event._process_one()
                        cr.commit()
            except Exception:
                _logger.exception(
                    "Inline processing of delivery webhook events %s failed; "
                    "the cron will retry them.", event_ids,
                )

        self.env.cr.postcommit.add(process_after_commit)

    @api.model
    def _cron_process_webhook_events(self, batch_size=50):
        self._recover_stale_processing_events()
        return self._process_due_events(batch_size=batch_size, commit_progress=True)

    @api.model
    def _recover_stale_processing_events(self):
        """Return work abandoned by a terminated worker to the durable retry queue."""
        connections = self.env["bs.delivery.connection"].sudo().search(
            [("active", "=", True), ("state", "=", "connected")]
        )
        recovered = self.browse()
        now = fields.Datetime.now()
        for connection in connections:
            deadline = now - timedelta(
                minutes=connection.webhook_processing_timeout_minutes
            )
            stale = self.sudo().search(
                [
                    ("connection_id", "=", connection.id),
                    ("state", "=", "processing"),
                    ("last_attempt_at", "<=", deadline),
                ]
            )
            if not stale:
                continue
            retryable = stale.filtered(
                lambda event: event.attempt_count < event.max_attempts
            )
            exhausted = stale - retryable
            retryable.write(
                {
                    "state": "retry",
                    "next_attempt_at": now,
                    "error_type": "integration_error",
                    "last_error": _(
                        "Processing was interrupted; the event was returned to the retry queue."
                    ),
                }
            )
            exhausted.write(
                {
                    "state": "failed",
                    "next_attempt_at": False,
                    "error_type": "integration_error",
                    "last_error": _(
                        "Processing was interrupted after the maximum number of attempts."
                    ),
                }
            )
            recovered |= stale
        if recovered:
            _logger.warning("Recovered %s stale delivery webhook event(s)", len(recovered))
        return len(recovered)

    @api.model
    def _cron_cleanup_webhook_events(self):
        """Apply per-connection PII and completed-event retention policies."""
        now = fields.Datetime.now()
        connections = (
            self.env["bs.delivery.connection"]
            .sudo()
            .with_context(active_test=False)
            .search([])
        )
        scrubbed = deleted = 0
        for connection in connections:
            raw_days = connection.webhook_raw_payload_retention_days
            if raw_days >= 0:
                raw_deadline = now - timedelta(days=raw_days)
                raw_events = self.sudo().search(
                    [
                        ("connection_id", "=", connection.id),
                        ("state", "in", ["done", "failed", "ignored"]),
                        ("received_at", "<", raw_deadline),
                        ("raw_payload", "!=", False),
                    ]
                )
                scrubbed += len(raw_events)
                raw_events.write({"raw_payload": False, "request_headers": False})

            event_deadline = now - timedelta(
                days=connection.webhook_event_retention_days
            )
            old_events = self.sudo().search(
                [
                    ("connection_id", "=", connection.id),
                    ("state", "in", ["done", "ignored"]),
                    ("received_at", "<", event_deadline),
                ]
            )
            deleted += len(old_events)
            old_events.unlink()
        return {"scrubbed": scrubbed, "deleted": deleted}

    @api.model
    def _process_due_events(self, *, batch_size=50, commit_progress=False):
        now = fields.Datetime.now()
        domain = [
            ("state", "in", ["queued", "retry"]),
            ("next_attempt_at", "<=", now),
            ("connection_id.active", "=", True),
            ("connection_id.state", "=", "connected"),
        ]
        remaining = self.search_count(domain)
        cron = self.env["ir.cron"]
        if commit_progress:
            cron._commit_progress(remaining=remaining)

        processed = 0
        while processed < batch_size:
            candidate = self.search(domain, order="next_attempt_at, id", limit=1)
            event = candidate.try_lock_for_update(limit=1)
            if not event:
                break
            with self.env.cr.savepoint():
                event._process_one()
            processed += 1
            if commit_progress and not cron._commit_progress(1):
                break
        return processed

    def _process_one(self):
        self.ensure_one()
        if self.state not in ("queued", "retry"):
            return False
        if self.attempt_count >= self.max_attempts:
            self.write(
                {
                    "state": "failed",
                    "next_attempt_at": False,
                    "error_type": "integration_error",
                    "last_error": _("The maximum number of attempts has been reached."),
                }
            )
            return False

        started_at = time.monotonic()
        attempt_number = self.attempt_count + 1
        self.write(
            {
                "state": "processing",
                "attempt_count": attempt_number,
                "last_attempt_at": fields.Datetime.now(),
                "next_attempt_at": False,
            }
        )
        process_log = self.env["bs.delivery.sync.log"].start_attempt(
            self.connection_id,
            direction="incoming",
            operation="webhook_process",
            webhook_event=self,
            external_id=self.external_id,
            request_payload=self.raw_payload,
            attempt_number=attempt_number,
            correlation_id=self.correlation_id,
        )
        try:
            result = self.connection_id.with_context(
                bs_delivery_correlation_id=self.correlation_id
            )._dispatch("process_webhook", self)
        except Exception as error:
            self._record_processing_failure(
                error,
                process_log,
                duration_ms=(time.monotonic() - started_at) * 1000,
            )
            return False

        now = fields.Datetime.now()
        self.write(
            {
                "state": "done",
                "processed_at": now,
                "next_attempt_at": False,
                "error_type": False,
                "last_error": False,
            }
        )
        self.connection_id.write({"last_sync_date": now})
        process_log.mark_success(
            response_payload=result,
            duration_ms=(time.monotonic() - started_at) * 1000,
        )
        return True

    def _record_processing_failure(self, error, process_log, *, duration_ms):
        self.ensure_one()
        if isinstance(error, DeliveryIntegrationError):
            error_types = dict(self._fields["error_type"].selection)
            error_type = error.code if error.code in error_types else "integration_error"
            retryable = error.retryable
        else:
            error_type = "unexpected_error"
            retryable = True
            _logger.exception(
                "Unexpected webhook processing failure for event %s",
                self.id,
            )

        error_message = mask_sensitive_text(str(error))[:4000]
        can_retry = retryable and self.attempt_count < self.max_attempts
        values = {
            "state": "retry" if can_retry else "failed",
            "error_type": error_type,
            "last_error": error_message,
            "processed_at": False,
        }
        if can_retry:
            base_seconds = self.connection_id.webhook_retry_base_seconds
            delay_seconds = min(base_seconds * (2 ** (self.attempt_count - 1)), 86_400)
            jitter_ceiling = max(1, delay_seconds // 5)
            delay_seconds = min(
                delay_seconds + secrets.randbelow(jitter_ceiling + 1),
                86_400,
            )
            values["next_attempt_at"] = fields.Datetime.now() + timedelta(
                seconds=delay_seconds
            )
        else:
            values["next_attempt_at"] = False
        self.write(values)
        process_log.mark_failed(error_message, duration_ms=duration_ms)

    def action_retry(self):
        self._check_management_access()
        retryable_events = self.filtered(lambda event: event.state == "failed")
        for event in retryable_events.sudo():
            event.write(
                {
                    "state": "retry",
                    "max_attempts": max(event.max_attempts, event.attempt_count + 1),
                    "next_attempt_at": fields.Datetime.now(),
                    "error_type": False,
                    "last_error": False,
                }
            )
        retryable_events._trigger_processing()
        return True

    def action_ignore(self):
        self._check_management_access()
        self.filtered(lambda event: event.state in ("queued", "retry", "failed")).sudo().write(
            {
                "state": "ignored",
                "next_attempt_at": False,
                "processed_at": fields.Datetime.now(),
            }
        )
        return True

    def _check_management_access(self):
        if not self.env.user.has_group(
            "bs_pos_delivery_core.group_delivery_integration_manager"
        ):
            raise AccessError(_("Delivery integration administrator access is required."))

    def action_view_logs(self):
        self.ensure_one()
        action = self.env.ref("bs_pos_delivery_core.action_bs_delivery_sync_log").read()[0]
        action["domain"] = [("webhook_event_id", "=", self.id)]
        return action
