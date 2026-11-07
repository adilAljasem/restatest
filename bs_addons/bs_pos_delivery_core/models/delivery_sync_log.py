from datetime import timedelta
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools import mask_sensitive_text, serialize_masked_payload


class DeliverySyncLog(models.Model):
    """Sanitized audit record for one integration attempt."""

    _name = "bs.delivery.sync.log"
    _description = "POS Delivery Synchronization Log"
    _order = "create_date desc, id desc"
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
        index=True,
        ondelete="set null",
        check_company=True,
    )
    webhook_event_id = fields.Many2one(
        "bs.delivery.webhook.event",
        string="Webhook Event",
        index=True,
        ondelete="set null",
        check_company=True,
    )
    direction = fields.Selection(
        [("incoming", "Incoming"), ("outgoing", "Outgoing")],
        required=True,
        index=True,
    )
    operation = fields.Char(required=True, index=True)
    external_id = fields.Char(index=True)
    correlation_id = fields.Char(
        required=True,
        index=True,
        copy=False,
        default=lambda self: str(uuid.uuid4()),
        help="Identifier shared by queue entries and HTTP attempts for one logical flow.",
    )
    request_url = fields.Char()
    request_method = fields.Selection(
        [
            ("GET", "GET"),
            ("POST", "POST"),
            ("PUT", "PUT"),
            ("PATCH", "PATCH"),
            ("DELETE", "DELETE"),
        ]
    )
    http_status = fields.Integer()
    duration_ms = fields.Float(string="Duration (ms)", digits=(16, 2))
    attempt_number = fields.Integer(default=1, required=True)
    status = fields.Selection(
        [("pending", "Pending"), ("success", "Success"), ("failed", "Failed")],
        default="pending",
        required=True,
        index=True,
    )
    error_message = fields.Text()
    request_payload = fields.Text()
    response_payload = fields.Text()
    can_retry_processing = fields.Boolean(compute="_compute_can_retry_processing")

    _attempt_positive = models.Constraint(
        "CHECK(attempt_number > 0)",
        "The attempt number must be greater than zero.",
    )
    _duration_non_negative = models.Constraint(
        "CHECK(duration_ms IS NULL OR duration_ms >= 0)",
        "The request duration cannot be negative.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._sanitize_values(vals) for vals in vals_list])

    def write(self, vals):
        return super().write(self._sanitize_values(vals))

    @api.model
    def _sanitize_values(self, values):
        values = dict(values)
        for field_name in ("request_payload", "response_payload"):
            if field_name in values:
                values[field_name] = serialize_masked_payload(values[field_name])
        for field_name in ("request_url", "error_message"):
            if field_name in values:
                values[field_name] = mask_sensitive_text(values[field_name])
        if values.get("request_method"):
            values["request_method"] = str(values["request_method"]).upper()
        return values

    @api.constrains("connection_id", "binding_id", "webhook_event_id")
    def _check_related_record_connections(self):
        for log in self:
            if log.binding_id and log.binding_id.connection_id != log.connection_id:
                raise ValidationError(
                    _("The synchronization log and order binding must use the same connection.")
                )
            if (
                log.webhook_event_id
                and log.webhook_event_id.connection_id != log.connection_id
            ):
                raise ValidationError(
                    _("The synchronization log and webhook event must use the same connection.")
                )

    def _retry_source(self):
        """Return the linked, currently retryable source; never infer it from external_id."""
        self.ensure_one()
        if self.status != "failed" or self.company_id not in self.env.companies:
            return None, False
        if self.direction == "outgoing" and self.operation.startswith("outbox:"):
            job = self.env["bs.delivery.outbox"].search([
                ("connection_id", "=", self.connection_id.id),
                ("binding_id", "=", self.binding_id.id),
                ("correlation_id", "=", self.correlation_id),
                ("operation", "=", self.operation.removeprefix("outbox:")),
                ("state", "=", "failed"),
            ], limit=1)
            return ("outbox", job) if job else (None, False)
        if self.direction != "incoming":
            return None, False
        event = self.webhook_event_id
        if event and event.connection_id != self.connection_id:
            return None, False
        binding = self.binding_id
        if binding and binding.connection_id != self.connection_id:
            return None, False
        if event and binding and event.binding_id != binding:
            return None, False
        order = self.env["bs.delivery.order"]
        if binding:
            order = order.search([
                ("binding_id", "=", binding.id),
                ("connection_id", "=", self.connection_id.id),
            ], limit=1)
        if not order and event:
            order = event.delivery_order_id
        pos_processing_log = self.operation in (
            "core:pos_processing", "core:pos_processing_retry",
        )
        if (pos_processing_log and order and order.connection_id == self.connection_id
                and order.company_id == self.company_id
                and order.pos_processing_state in ("blocked", "waiting_session")
                and (order.state in ("accepted", "preparing", "ready", "picked_up")
                     or (order.state == "received"
                         and order.connection_id.delivery_accept_action == "accept"))):
            return "order", order
        if (not pos_processing_log and event
                and event.state == "failed" and event.connection_id.active
                and event.connection_id.state == "connected"
                and event.sudo().raw_payload):
            return "event", event
        return None, False

    @api.depends("status")
    def _compute_can_retry_processing(self):
        for log in self:
            log.can_retry_processing = log.status == "failed"

    def _retry_history_action(self):
        action = self.env.ref("bs_pos_delivery_core.action_bs_delivery_sync_log").sudo().read()[0]
        field_name = "webhook_event_id" if self.webhook_event_id else "binding_id"
        source = self.webhook_event_id or self.binding_id
        action["domain"] = ([(field_name, "=", source.id)] if source else
                            [("correlation_id", "=", self.correlation_id)])
        return action

    def _retry_unlinked_failure(self):
        """Provider addons may resume authenticated inbound HTTP attempts."""
        self.ensure_one()
        raise UserError(_("This failed operation has no safe retry path."))

    can_force_send = fields.Boolean(compute="_compute_can_force_send")

    @api.depends("status", "operation", "direction", "request_method", "request_url")
    def _compute_can_force_send(self):
        """Provider addons enable the button for their failed new-order logs."""
        for log in self:
            log.can_force_send = False

    def _force_send_existing_order(self):
        """Provider hook: the delivery order already created from this request."""
        self.ensure_one()
        return self.env["bs.delivery.order"]

    def _force_send_unlinked_failure(self):
        """Provider hook: rebuild the order from the saved request, relaxed."""
        self.ensure_one()
        raise UserError(_("This failed operation cannot be force-sent."))

    def action_force_send_to_branch(self):
        """Send a rejected platform order to its branch POS (cashier and kitchen).

        The order is rebuilt from the saved request.  Each provider relaxes
        the checks a manager may overrule (amounts, availability, quantity
        limits); Odoo's own prices and taxes then decide the amounts and the
        order follows the normal flow: POS order, payment, invoice, cashier
        receipt and kitchen ticket.  What cannot be forced (unknown product,
        unmapped branch) is still reported.
        """
        self.ensure_one()
        self.check_access("read")
        user = self.env.user
        if not (user.has_group("bs_pos_delivery_core.group_delivery_manager")
                or user.has_group("bs_pos_delivery_core.group_delivery_integration_manager")):
            raise AccessError(_("Only a delivery manager can force-send an order to a branch."))
        if self.company_id not in self.env.companies:
            raise AccessError(_("The synchronization log belongs to an unavailable company."))
        self.env.cr.execute("SELECT id FROM bs_delivery_sync_log WHERE id = %s FOR UPDATE", [self.id])
        self.invalidate_recordset()
        if not self.can_force_send:
            raise UserError(_("Only a failed new-order request can be force-sent."))
        existing = self._force_send_existing_order()
        if existing:
            raise UserError(_(
                "This order was already sent to the branch as %s.", existing.display_name,
            ))
        original_error = self.error_message or ""
        self.sudo().write({"attempt_number": self.attempt_number + 1})
        try:
            with self.env.cr.savepoint():
                result = self.with_context(bs_delivery_force_send=True)._force_send_unlinked_failure()
        except AccessError:
            raise
        except Exception as error:
            message = str(error)
            self.sudo().write({"status": "failed", "error_message": message})
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Force send failed"),
                    "message": message,
                    "type": "danger",
                    "sticky": True,
                    "next": {"type": "ir.actions.client", "tag": "reload"},
                },
            }
        order = self.env["bs.delivery.order"].browse(
            (result or {}).get("delivery_order_id")
        ).exists()
        if order:
            order.message_post(body=_(
                "Force-sent to the branch by %(user)s from the delivery log, although "
                "Odoo had rejected the platform's request: %(error)s. Amounts follow "
                "the Odoo product prices and taxes.",
                user=user.name, error=original_error or "-",
            ))
        self.mark_success(response_payload={**(result or {}), "forced": True})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Order sent to the branch"),
                "message": (
                    _("%s is on the branch POS delivery board, waiting for acceptance.",
                      order.display_name)
                    if order and order.state == "received" else
                    _("%s was sent to the branch cashier and kitchen.",
                      order.display_name if order else _("The order"))
                ),
                "type": "success",
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_retry_processing_bulk(self):
        """Retry every selected failed log; one failure does not stop the rest."""
        if not self.env.user.has_group(
            "bs_pos_delivery_core.group_delivery_integration_manager"
        ):
            raise AccessError(_("Delivery integration administrator access is required."))
        done = failed = skipped = 0
        errors = []
        for log in self.sorted("id"):
            if log.status != "failed":
                skipped += 1
                continue
            try:
                with self.env.cr.savepoint():
                    log.action_retry_processing()
            except Exception as error:  # noqa: BLE001 - report and continue
                failed += 1
                errors.append("#%s: %s" % (log.id, error))
                continue
            log.invalidate_recordset(["status", "error_message"])
            if log.status == "failed":
                failed += 1
                errors.append("#%s: %s" % (log.id, log.error_message or ""))
            else:
                done += 1
        message = _(
            "%(done)s succeeded, %(failed)s still failing, %(skipped)s skipped.",
            done=done, failed=failed, skipped=skipped,
        )
        if errors:
            message += "\n" + "\n".join(errors[:10])
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Retry selected logs"),
                "message": message,
                "type": "warning" if failed else "success",
                "sticky": bool(failed),
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_retry_processing(self):
        self.ensure_one()
        self.check_access("read")
        if not self.env.user.has_group(
            "bs_pos_delivery_core.group_delivery_integration_manager"
        ):
            raise AccessError(_("Delivery integration administrator access is required."))
        if self.company_id not in self.env.companies:
            raise AccessError(_("The synchronization log belongs to an unavailable company."))
        self.env.cr.execute("SELECT id FROM bs_delivery_sync_log WHERE id = %s FOR UPDATE", [self.id])
        self.invalidate_recordset()
        if self.status != "failed":
            raise UserError(_("Only failed synchronization logs can be retried."))
        source_type, source = self._retry_source()
        if not source:
            if self.binding_id or self.webhook_event_id:
                raise UserError(_("The linked operation is no longer retryable."))
            self.sudo().write({"attempt_number": self.attempt_number + 1})
            try:
                with self.env.cr.savepoint():
                    result = self._retry_unlinked_failure()
            except AccessError:
                raise
            except Exception as error:
                self.sudo().write({"status": "failed", "error_message": str(error)})
            else:
                self.mark_success(response_payload=result)
            return {"type": "ir.actions.client", "tag": "reload"}
        table = {"order": "bs_delivery_order", "event": "bs_delivery_webhook_event",
                 "outbox": "bs_delivery_outbox"}[source_type]
        self.env.cr.execute("SELECT id FROM %s WHERE id = %%s FOR UPDATE" % table, [source.id])
        source.invalidate_recordset()
        self.invalidate_recordset()
        current_type, current_source = self._retry_source()
        if current_type != source_type or current_source != source:
            raise UserError(_("The source has already been processed or retried."))
        if source_type == "event":
            source.action_retry()
            return self._retry_history_action()
        if source_type == "outbox":
            source.action_retry()
            return self._retry_history_action()

        self.sudo().write({"attempt_number": self.attempt_number + 1})
        try:
            with self.env.cr.savepoint():
                source.action_retry_pos_processing()
                source.invalidate_recordset(["pos_order_id", "pos_processing_state"])
                invoice = source.pos_order_id.account_move
                if (source.pos_processing_state != "done" or not source.pos_order_id
                        or source.pos_order_id.state != "done" or not invoice
                        or invoice.state != "posted"
                        or not source.currency_id.is_zero(invoice.amount_residual)):
                    raise UserError(_("POS processing did not finish payment and invoicing."))
        except AccessError:
            raise
        except Exception as error:
            self.sudo().write({"status": "failed", "error_message": str(error)})
            return {"type": "ir.actions.client", "tag": "reload"}
        self.mark_success(response_payload={
            "pos_order_id": source.pos_order_id.id,
            "pos_processing_state": source.pos_processing_state,
        })
        return {"type": "ir.actions.client", "tag": "reload"}

    @api.model
    def start_attempt(
        self,
        connection,
        *,
        direction,
        operation,
        binding=None,
        webhook_event=None,
        external_id=None,
        request_url=None,
        request_method=None,
        request_payload=None,
        attempt_number=1,
        correlation_id=None,
    ):
        connection.ensure_one()
        return self.sudo().create(
            {
                "connection_id": connection.id,
                "binding_id": binding.id if binding else False,
                "webhook_event_id": webhook_event.id if webhook_event else False,
                "direction": direction,
                "operation": operation,
                "external_id": external_id,
                "correlation_id": correlation_id
                or self.env.context.get("bs_delivery_correlation_id")
                or str(uuid.uuid4()),
                "request_url": request_url,
                "request_method": request_method,
                "request_payload": request_payload,
                "attempt_number": attempt_number,
                "status": "pending",
            }
        )

    def mark_success(self, *, response_payload=None, http_status=None, duration_ms=None):
        return self.sudo().write(
            {
                "status": "success",
                "response_payload": response_payload,
                "http_status": http_status,
                "duration_ms": duration_ms,
                "error_message": False,
            }
        )

    def mark_failed(
        self,
        error_message,
        *,
        response_payload=None,
        http_status=None,
        duration_ms=None,
    ):
        return self.sudo().write(
            {
                "status": "failed",
                "response_payload": response_payload,
                "http_status": http_status,
                "duration_ms": duration_ms,
                "error_message": error_message,
            }
        )

    @api.model
    def _cron_cleanup_sync_logs(self):
        """Remove audit records according to each connection's retention setting."""
        now = fields.Datetime.now()
        deleted = 0
        connections = (
            self.env["bs.delivery.connection"]
            .sudo()
            .with_context(active_test=False)
            .search([])
        )
        for connection in connections:
            deadline = now - timedelta(days=connection.sync_log_retention_days)
            logs = self.sudo().search(
                [
                    ("connection_id", "=", connection.id),
                    ("create_date", "<", deadline),
                ]
            )
            deleted += len(logs)
            logs.unlink()
        return deleted
