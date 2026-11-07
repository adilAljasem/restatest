from datetime import datetime, timezone

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .pos_config import KITCHEN_FLOW_PRIORITIES


LOG_STATUSES = [
    ("success", "Success"),
    ("warning", "Completed with Warnings"),
    ("failed", "Failed"),
]

STEP_STATUSES = [
    ("success", "Success"),
    ("error", "Error"),
    ("skipped", "Skipped"),
    ("info", "Information"),
]


def _safe_float(value, default=0.0):
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def _safe_datetime(value):
    if not value:
        return False
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise ValidationError(_("Invalid kitchen flow timestamp: %s", value)) from error
    if result.tzinfo:
        result = result.astimezone(timezone.utc).replace(tzinfo=None)
    return result


class PosKitchenFlowLog(models.Model):
    _name = "bs.pos.kitchen.flow.log"
    _description = "POS Kitchen Flow Diagnostic"
    _order = "started_at desc, id desc"

    name = fields.Char(string="Trace", required=True, readonly=True, index=True)
    transaction_uuid = fields.Char(required=True, readonly=True, index=True)
    config_id = fields.Many2one(
        "pos.config",
        string="Point of Sale",
        required=True,
        readonly=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="config_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )
    session_id = fields.Many2one(
        "pos.session",
        string="Session",
        readonly=True,
        ondelete="set null",
        index=True,
    )
    order_id = fields.Many2one(
        "pos.order",
        string="Order",
        readonly=True,
        ondelete="set null",
        index=True,
    )
    order_uuid = fields.Char(string="Order UUID", readonly=True, index=True)
    order_reference = fields.Char(string="Order Reference", readonly=True, index=True)
    user_id = fields.Many2one(
        "res.users",
        string="POS User",
        required=True,
        readonly=True,
        ondelete="restrict",
    )
    device_identifier = fields.Char(readonly=True)
    priority = fields.Selection(
        selection=KITCHEN_FLOW_PRIORITIES,
        required=True,
        readonly=True,
    )
    status = fields.Selection(
        selection=LOG_STATUSES,
        required=True,
        readonly=True,
        index=True,
    )
    started_at = fields.Datetime(required=True, readonly=True, index=True)
    completed_at = fields.Datetime(required=True, readonly=True)
    total_duration_ms = fields.Float(
        string="Total Duration (ms)",
        digits=(16, 3),
        required=True,
        readonly=True,
    )
    error_message = fields.Text(readonly=True)
    client_summary = fields.Json(readonly=True)
    step_ids = fields.One2many(
        "bs.pos.kitchen.flow.step",
        "log_id",
        string="Timed Operations",
        readonly=True,
    )

    _transaction_uuid_unique = models.Constraint(
        "unique(transaction_uuid)",
        "The kitchen flow trace identifier must be unique.",
    )

    @api.model
    def create_from_ui(self, payload):
        """Persist one browser trace without ever granting direct create access to POS users."""
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessError(_("Only Point of Sale users can create kitchen flow diagnostics."))
        if not isinstance(payload, dict):
            raise ValidationError(_("The kitchen flow diagnostic payload is invalid."))

        transaction_uuid = str(payload.get("transaction_uuid") or "").strip()[:128]
        if not transaction_uuid:
            raise ValidationError(_("A kitchen flow trace identifier is required."))

        existing = self.sudo().search(
            [("transaction_uuid", "=", transaction_uuid)],
            limit=1,
        )
        if existing:
            return existing.id

        try:
            config_id = int(payload.get("config_id"))
        except (TypeError, ValueError) as error:
            raise ValidationError(_("A valid Point of Sale configuration is required.")) from error
        config = self.env["pos.config"].browse(config_id).exists()
        if not config:
            raise ValidationError(_("The Point of Sale configuration no longer exists."))

        session = self.env["pos.session"].browse(int(payload.get("session_id") or 0)).exists()
        if session and session.config_id != config:
            raise ValidationError(_("The POS session does not belong to the selected configuration."))

        order_uuid = str(payload.get("order_uuid") or "").strip()[:128]
        order = self.env["pos.order"]
        try:
            order_server_id = int(payload.get("order_server_id") or 0)
        except (TypeError, ValueError):
            order_server_id = 0
        if order_server_id:
            order = order.browse(order_server_id).exists()
        if not order and order_uuid:
            order = self.env["pos.order"].search([("uuid", "=", order_uuid)], limit=1)
        if order and order.config_id != config:
            order = self.env["pos.order"]

        priority_values = dict(KITCHEN_FLOW_PRIORITIES)
        priority = payload.get("priority")
        if priority not in priority_values:
            priority = "printer_first"
        status = payload.get("status")
        if status not in dict(LOG_STATUSES):
            status = "failed"

        steps = []
        for index, step in enumerate(payload.get("steps") or [], start=1):
            if not isinstance(step, dict):
                continue
            step_status = step.get("status")
            if step_status not in dict(STEP_STATUSES):
                step_status = "info"
            details = step.get("details")
            if not isinstance(details, (dict, list, str, int, float, bool, type(None))):
                details = str(details)
            steps.append(
                (
                    0,
                    0,
                    {
                        "sequence": int(step.get("sequence") or index),
                        "code": str(step.get("code") or "operation")[:128],
                        "name": str(step.get("name") or step.get("code") or _("Operation"))[:256],
                        "parent_code": str(step.get("parent_code") or "")[:128],
                        "category": str(step.get("category") or "client")[:64],
                        "status": step_status,
                        "started_at": _safe_datetime(step.get("started_at")),
                        "completed_at": _safe_datetime(step.get("completed_at")),
                        "duration_ms": _safe_float(step.get("duration_ms")),
                        "details_json": details,
                        "error_message": str(step.get("error_message") or "")[:10000],
                    },
                )
            )

        started_at = _safe_datetime(payload.get("started_at"))
        completed_at = _safe_datetime(payload.get("completed_at"))
        if not started_at or not completed_at:
            raise ValidationError(_("Kitchen flow start and completion timestamps are required."))

        values = {
            "name": str(payload.get("name") or transaction_uuid)[:256],
            "transaction_uuid": transaction_uuid,
            "config_id": config.id,
            "session_id": session.id,
            "order_id": order.id,
            "order_uuid": order_uuid,
            "order_reference": str(payload.get("order_reference") or "")[:256],
            "user_id": self.env.user.id,
            "device_identifier": str(payload.get("device_identifier") or "")[:128],
            "priority": priority,
            "status": status,
            "started_at": started_at,
            "completed_at": completed_at,
            "total_duration_ms": _safe_float(payload.get("total_duration_ms")),
            "error_message": str(payload.get("error_message") or "")[:10000],
            "client_summary": payload.get("client_summary")
            if isinstance(payload.get("client_summary"), dict)
            else {},
            "step_ids": steps,
        }
        return self.sudo().create(values).id


class PosKitchenFlowStep(models.Model):
    _name = "bs.pos.kitchen.flow.step"
    _description = "POS Kitchen Flow Timed Operation"
    _order = "sequence, id"

    log_id = fields.Many2one(
        "bs.pos.kitchen.flow.log",
        required=True,
        readonly=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="log_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )
    sequence = fields.Integer(required=True, readonly=True)
    code = fields.Char(required=True, readonly=True, index=True)
    name = fields.Char(required=True, readonly=True)
    parent_code = fields.Char(readonly=True)
    category = fields.Char(readonly=True, index=True)
    status = fields.Selection(
        selection=STEP_STATUSES,
        required=True,
        readonly=True,
        index=True,
    )
    started_at = fields.Datetime(readonly=True)
    completed_at = fields.Datetime(readonly=True)
    duration_ms = fields.Float(digits=(16, 3), required=True, readonly=True)
    details_json = fields.Json(string="Details", readonly=True)
    error_message = fields.Text(readonly=True)
