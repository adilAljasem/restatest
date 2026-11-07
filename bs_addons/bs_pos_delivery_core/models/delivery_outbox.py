import hashlib
import logging
import secrets
import uuid
from datetime import timedelta

import psycopg2

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..exceptions import DeliveryIntegrationError
from ..tools import mask_sensitive_text, serialize_masked_payload


_logger = logging.getLogger(__name__)


class DeliveryOutbox(models.Model):
    """Durable queue for provider-facing order state mutations."""

    _name = "bs.delivery.outbox"
    _description = "POS Delivery Outgoing Job"
    _rec_name = "correlation_id"
    _order = "priority desc, next_attempt_at, id"
    _check_company_auto = True

    MUTATING_OPERATIONS = frozenset(
        {
            "update_order",
            "accept_order",
            "reject_order",
            "cancel_order",
            "mark_order_preparing",
            "mark_order_ready",
        }
    )

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
        required=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    external_id = fields.Char(
        related="binding_id.external_order_id",
        store=True,
        index=True,
        readonly=True,
    )
    operation = fields.Selection(
        [
            ("update_order", "Update Order"),
            ("accept_order", "Accept Order"),
            ("reject_order", "Reject Order"),
            ("cancel_order", "Cancel Order"),
            ("mark_order_preparing", "Mark Preparing"),
            ("mark_order_ready", "Mark Ready"),
        ],
        required=True,
        index=True,
    )
    status_value = fields.Char(copy=False)
    reason = fields.Text(copy=False)
    idempotency_key = fields.Char(required=True, index=True, copy=False)
    correlation_id = fields.Char(
        required=True,
        index=True,
        copy=False,
        default=lambda self: str(uuid.uuid4()),
    )
    state = fields.Selection(
        [
            ("queued", "Queued"),
            ("processing", "Processing"),
            ("retry", "Waiting for Retry"),
            ("done", "Done"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        default="queued",
        index=True,
        copy=False,
    )
    priority = fields.Integer(default=10, required=True, index=True)
    attempt_count = fields.Integer(default=0, required=True, readonly=True, copy=False)
    max_attempts = fields.Integer(default=5, required=True, copy=False)
    last_attempt_at = fields.Datetime(readonly=True, copy=False)
    next_attempt_at = fields.Datetime(default=fields.Datetime.now, index=True, copy=False)
    processed_at = fields.Datetime(readonly=True, copy=False)
    last_error = fields.Text(readonly=True, copy=False)
    result_payload = fields.Text(readonly=True, copy=False)

    _idempotency_unique = models.Constraint(
        "UNIQUE(connection_id, idempotency_key)",
        "An outgoing operation can only be queued once per delivery connection.",
    )
    _attempt_values_valid = models.Constraint(
        "CHECK(attempt_count >= 0 AND max_attempts > 0)",
        "Outgoing job attempt values are invalid.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._sanitize_values(vals) for vals in vals_list])

    def write(self, vals):
        return super().write(self._sanitize_values(vals))

    @api.model
    def _sanitize_values(self, values):
        values = dict(values)
        if "last_error" in values:
            values["last_error"] = mask_sensitive_text(values["last_error"])
        if "result_payload" in values:
            values["result_payload"] = serialize_masked_payload(values["result_payload"])
        return values

    @api.constrains("connection_id", "binding_id")
    def _check_binding_connection(self):
        for job in self:
            if job.binding_id.connection_id != job.connection_id:
                raise ValidationError(
                    _("The outgoing job and order binding must use the same connection.")
                )

    @api.model
    def enqueue(
        self,
        connection,
        operation,
        binding,
        *,
        status_value=None,
        reason=None,
        idempotency_key=None,
        priority=10,
    ):
        connection.ensure_one()
        binding.ensure_one()
        if operation not in self.MUTATING_OPERATIONS:
            raise ValidationError(_("This operation cannot be queued: %s") % operation)
        if binding.connection_id != connection:
            raise ValidationError(
                _("The order binding does not belong to this delivery connection.")
            )
        if operation == "update_order" and not str(status_value or "").strip():
            raise ValidationError(_("A status value is required to queue an order update."))

        raw_idempotency = str(idempotency_key or uuid.uuid4())
        normalized_key = hashlib.sha256(raw_idempotency.encode()).hexdigest()
        domain = [
            ("connection_id", "=", connection.id),
            ("idempotency_key", "=", normalized_key),
        ]
        existing = self.sudo().search(domain, limit=1)
        if existing:
            return existing, False

        values = {
            "connection_id": connection.id,
            "binding_id": binding.id,
            "operation": operation,
            "status_value": str(status_value or "").strip() or False,
            "reason": str(reason or "").strip() or False,
            "idempotency_key": normalized_key,
            "correlation_id": str(uuid.uuid4()),
            "priority": priority,
            "max_attempts": connection.outbox_max_attempts,
            "state": "queued",
            "next_attempt_at": fields.Datetime.now(),
        }
        try:
            with self.env.cr.savepoint():
                job = self.sudo().create(values)
        except psycopg2.errors.UniqueViolation:
            job = self.sudo().search(domain, limit=1)
            if not job:
                raise
            return job, False

        binding.sudo().mark_sync_pending()
        job._trigger_processing()
        return job, True

    def _trigger_processing(self):
        cron = self.env.ref(
            "bs_pos_delivery_core.ir_cron_bs_delivery_process_outbox",
            raise_if_not_found=False,
        )
        if cron:
            cron.sudo()._trigger()

    @api.model
    def _cron_process_outbox(self, batch_size=50):
        self._recover_stale_processing_jobs()
        return self._process_due_jobs(batch_size=batch_size, commit_progress=True)

    @api.model
    def _process_due_jobs(self, *, batch_size=50, commit_progress=False):
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
            # Only the first unresolved mutation of each order is eligible.
            # A delayed/failed acceptance must also block a higher-priority ready
            # update, without blocking another order's independent work.
            first_ids = [
                first_id
                for _binding, first_id in self._read_group(
                    [
                        ("state", "not in", ["done", "cancelled"]),
                        ("connection_id.active", "=", True),
                        ("connection_id.state", "=", "connected"),
                    ],
                    ["binding_id"],
                    ["id:min"],
                )
            ]
            candidate = self.search(
                domain + [("id", "in", first_ids)],
                order="priority desc, next_attempt_at, id",
                limit=1,
            )
            job = candidate.try_lock_for_update(limit=1)
            if not job:
                break
            with self.env.cr.savepoint():
                job._process_one()
            processed += 1
            if commit_progress and not cron._commit_progress(1):
                break
        return processed

    def _process_one(self):
        self.ensure_one()
        if self.state not in ("queued", "retry"):
            return False
        self.env.cr.execute(
            "SELECT id FROM bs_delivery_order_binding WHERE id = %s "
            "FOR UPDATE SKIP LOCKED",
            [self.binding_id.id],
        )
        if not self.env.cr.fetchone():
            return False
        if self.search_count(
            [
                ("binding_id", "=", self.binding_id.id),
                ("id", "<", self.id),
                ("state", "not in", ["done", "cancelled"]),
            ],
            limit=1,
        ):
            return False
        if self.operation == "accept_order" or (
            self.operation == "update_order" and self.status_value == "accepted"
        ):
            canonical = self.env["bs.delivery.order"].sudo().search([
                ("binding_id", "=", self.binding_id.id),
            ], limit=1)
            if canonical and canonical.state in ("cancelled", "rejected"):
                self.write({"state": "cancelled", "next_attempt_at": False})
                return False
        waiting_order = self._waiting_acceptance_order()
        if (waiting_order and waiting_order.acceptance_deadline_at
                and fields.Datetime.now() >= waiting_order.acceptance_deadline_at):
            self._expire_waiting_acceptance(waiting_order)
            return False
        if self.attempt_count >= self.max_attempts:
            self._mark_exhausted()
            return False

        attempt_number = self.attempt_count + 1
        self.write(
            {
                "state": "processing",
                "attempt_count": attempt_number,
                "last_attempt_at": fields.Datetime.now(),
                "next_attempt_at": False,
            }
        )
        log = self.env["bs.delivery.sync.log"].start_attempt(
            self.connection_id,
            direction="outgoing",
            operation="outbox:%s" % self.operation,
            binding=self.binding_id,
            external_id=self.external_id,
            request_payload=self._operation_payload(),
            attempt_number=attempt_number,
            correlation_id=self.correlation_id,
        )
        try:
            result = self._dispatch_operation()
        except Exception as error:
            self._record_failure(error, log)
            return False

        now = fields.Datetime.now()
        self.write(
            {
                "state": "done",
                "processed_at": now,
                "next_attempt_at": False,
                "last_error": False,
                "result_payload": result,
            }
        )
        # A rejected order (or acceptance waiting for an open POS session) can
        # be acknowledged remotely without having a POS order. Binding
        # ``synced`` still means a real POS link, so do not violate its invariant.
        unresolved = self.sudo().search_count(
            [
                ("binding_id", "=", self.binding_id.id),
                ("id", "!=", self.id),
                ("state", "not in", ["done", "cancelled"]),
            ],
            limit=1,
        )
        if not unresolved and self.binding_id.pos_order_id:
            self.binding_id.sudo().mark_sync_success()
        elif not unresolved:
            self.binding_id.sudo().write({"last_sync_date": now, "last_error": False})
        canonical = (
            self.env["bs.delivery.order"]
            .sudo()
            .search([("binding_id", "=", self.binding_id.id)], limit=1)
        )
        if canonical and not unresolved:
            canonical._with_delivery_internal_update().write(
                {"sync_state": "synced", "last_sync_at": now, "last_sync_error": False}
            )
        self.connection_id.sudo().write({"last_sync_date": now})
        log.mark_success(response_payload=result)
        return True

    def _dispatch_operation(self):
        self.ensure_one()
        connection = self.connection_id.with_context(
            bs_delivery_correlation_id=self.correlation_id,
            bs_delivery_outbox_job_id=self.id,
        )
        if self.operation == "update_order":
            return connection._dispatch(
                self.operation,
                self.binding_id,
                self.status_value,
                reason=self.reason or None,
            )
        if self.operation in ("reject_order", "cancel_order"):
            return connection._dispatch(
                self.operation,
                self.binding_id,
                reason=self.reason or None,
            )
        return connection._dispatch(self.operation, self.binding_id)

    def _waiting_acceptance_order(self):
        self.ensure_one()
        if self.operation not in ("accept_order", "update_order") or (
            self.operation == "update_order" and self.status_value != "accepted"
        ):
            return self.env["bs.delivery.order"]
        return self.env["bs.delivery.order"].sudo().search([
            ("binding_id", "=", self.binding_id.id),
            ("acceptance_deadline_at", "!=", False),
            ("state", "in", ["accepted", "preparing", "ready", "picked_up"]),
        ], limit=1)

    def _expire_waiting_acceptance(self, order):
        message = _("Aggregator acceptance expired before the provider confirmed it. Review the order.")
        self.write({"state": "failed", "next_attempt_at": False, "last_error": message})
        self.binding_id.sudo().mark_sync_error(message)
        self._flag_waiting_acceptance_error(order, message)

    def _operation_payload(self):
        self.ensure_one()
        return {
            "operation": self.operation,
            "external_order_id": self.external_id,
            "status": self.status_value or None,
            "reason_supplied": bool(self.reason),
        }

    def _record_failure(self, error, log):
        self.ensure_one()
        retryable = error.retryable if isinstance(error, DeliveryIntegrationError) else True
        if not isinstance(error, DeliveryIntegrationError):
            _logger.exception("Unexpected outgoing job failure for job %s", self.id)
        message = mask_sensitive_text(str(error))[:4000]
        can_retry = retryable and self.attempt_count < self.max_attempts
        waiting_order = self._waiting_acceptance_order()
        values = {
            "state": "retry" if can_retry else "failed",
            "last_error": message,
            "processed_at": False,
        }
        if can_retry:
            base_seconds = self.connection_id.outbox_retry_base_seconds
            delay_seconds = min(base_seconds * (2 ** (self.attempt_count - 1)), 86_400)
            if waiting_order:
                delay_seconds = min(delay_seconds, 10)
            jitter_ceiling = max(1, delay_seconds // 5)
            delay_seconds = min(
                delay_seconds + secrets.randbelow(jitter_ceiling + 1),
                86_400,
            )
            values["next_attempt_at"] = fields.Datetime.now() + timedelta(seconds=delay_seconds)
        else:
            values["next_attempt_at"] = False
            self.binding_id.sudo().mark_sync_error(message)
            if waiting_order:
                self._flag_waiting_acceptance_error(waiting_order, message)
        self.write(values)
        log.mark_failed(message)
        if can_retry and waiting_order:
            cron = self.env.ref(
                "bs_pos_delivery_core.ir_cron_bs_delivery_process_outbox",
                raise_if_not_found=False,
            )
            if cron:
                cron.sudo()._trigger(at=values["next_attempt_at"])

    def _mark_exhausted(self):
        message = _("The maximum number of outgoing attempts has been reached.")
        self.write(
            {
                "state": "failed",
                "next_attempt_at": False,
                "last_error": message,
            }
        )
        self.binding_id.sudo().mark_sync_error(message)
        waiting_order = self._waiting_acceptance_order()
        if waiting_order:
            self._flag_waiting_acceptance_error(waiting_order, message)

    def _flag_waiting_acceptance_error(self, order, message):
        order._set_pos_processing_result("blocked", message)
        order.sudo()._with_delivery_internal_update().write({
            "sync_state": "error", "last_sync_error": message,
        })
        order.message_post(body=message)
        order._notify_pos_board("state_changed")

    @api.model
    def _recover_stale_processing_jobs(self):
        connections = (
            self.env["bs.delivery.connection"]
            .sudo()
            .search([("active", "=", True), ("state", "=", "connected")])
        )
        recovered = self.browse()
        now = fields.Datetime.now()
        for connection in connections:
            deadline = now - timedelta(minutes=connection.outbox_processing_timeout_minutes)
            stale = self.sudo().search(
                [
                    ("connection_id", "=", connection.id),
                    ("state", "=", "processing"),
                    ("last_attempt_at", "<=", deadline),
                ]
            )
            retryable = stale.filtered(lambda job: job.attempt_count < job.max_attempts)
            exhausted = stale - retryable
            retryable.write(
                {
                    "state": "retry",
                    "next_attempt_at": now,
                    "last_error": _(
                        "Processing was interrupted; the job was returned to the retry queue."
                    ),
                }
            )
            for job in exhausted:
                job._mark_exhausted()
            recovered |= stale
        if recovered:
            _logger.warning("Recovered %s stale delivery outgoing job(s)", len(recovered))
        return len(recovered)

    @api.model
    def _cron_cleanup_outbox_jobs(self):
        now = fields.Datetime.now()
        deleted = 0
        connections = (
            self.env["bs.delivery.connection"].sudo().with_context(active_test=False).search([])
        )
        for connection in connections:
            deadline = now - timedelta(days=connection.outbox_retention_days)
            jobs = self.sudo().search(
                [
                    ("connection_id", "=", connection.id),
                    ("state", "in", ["done", "cancelled"]),
                    ("create_date", "<", deadline),
                ]
            )
            deleted += len(jobs)
            jobs.unlink()
        return deleted

    def action_retry(self):
        self._check_management_access()
        retryable_jobs = self.filtered(lambda job: job.state == "failed")
        for job in retryable_jobs.sudo():
            job.write(
                {
                    "state": "retry",
                    "max_attempts": max(job.max_attempts, job.attempt_count + 1),
                    "next_attempt_at": fields.Datetime.now(),
                    "last_error": False,
                }
            )
        retryable_jobs.mapped("binding_id").sudo().mark_sync_pending()
        retryable_jobs._trigger_processing()
        return True

    def action_cancel(self):
        self._check_management_access()
        self.filtered(lambda job: job.state in ("queued", "retry", "failed")).sudo().write(
            {
                "state": "cancelled",
                "next_attempt_at": False,
                "processed_at": fields.Datetime.now(),
            }
        )
        return True

    def _check_management_access(self):
        if not self.env.user.has_group("bs_pos_delivery_core.group_delivery_integration_manager"):
            raise AccessError(_("Delivery integration administrator access is required."))
