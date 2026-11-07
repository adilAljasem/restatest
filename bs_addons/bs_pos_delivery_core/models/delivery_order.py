import hashlib
import json
import uuid
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools import mask_sensitive_text


DELIVERY_ORDER_STATES = [
    ("received", "Received"),
    ("accepted", "Accepted"),
    ("preparing", "Preparing"),
    ("ready", "Ready for Pick-up"),
    ("picked_up", "Picked Up"),
    ("delivered", "Delivered"),
    ("rejected", "Rejected"),
    ("cancelled", "Cancelled"),
]

DELIVERY_TRANSITION_SOURCES = [
    ("manual", "Manual"),
    ("webhook", "Webhook"),
    ("automatic", "Automatic Rule"),
    ("provider", "Provider API"),
    ("system", "System"),
]

# An identity token, rather than a boolean context flag, prevents RPC callers
# from forging the internal state-write context and bypassing transition audit.
_STATE_TRANSITION_TOKEN = object()
_ACTOR_OVERRIDE_TOKEN = object()
_FINANCIAL_CORRECTION_TOKEN = object()
_INTERNAL_ORDER_UPDATE_TOKEN = object()

_DELIVERY_FINANCIAL_FIELDS = frozenset(
    {
        "currency_id",
        "items_subtotal",
        "discount_amount",
        "untaxed_discounts",
        "delivery_fee",
        "service_fee",
        "small_order_fee",
        "tip_amount",
        "vat_amount",
        "amount_total",
        "commission_rate",
        "commission_amount",
        "net_amount",
        "line_ids",
        "payment_allocation_ids",
    }
)

_CANONICAL_ORDER_FIELDS = frozenset(
    {
        "name",
        "external_order_id",
        "external_reference",
        "external_source",
        "external_status",
        "deliverect_receipt_id",
        "connection_id",
        "store_binding_id",
        "binding_id",
        "webhook_event_id",
        "pos_order_id",
        "pos_processing_state",
        "pos_processing_error",
        "session_wait_started_at",
        "session_activity_id",
        "acceptance_deadline_at",
        "pos_refund_order_id",
        "state",
        "fulfillment_type",
        "is_prepaid",
        "remote_payment_method",
        "contract_version",
        "contract_snapshot_hash",
        "core_last_provider_update_at",
        "scheduled_at",
        "customer_name",
        "customer_phone",
        "customer_email",
        "delivery_address",
        "customer_note",
        "driver_name",
        "driver_phone",
        "driver_eta_minutes",
        "driver_latitude",
        "driver_longitude",
        "driver_tracking_url",
        "delivery_latitude",
        "delivery_longitude",
        "received_at",
        "accepted_at",
        "preparing_at",
        "ready_at",
        "picked_up_at",
        "delivered_at",
        "rejected_at",
        "cancelled_at",
        "reject_reason_id",
        "rejection_note",
        "delivery_invoice_partner_id",
        "late_threshold_minutes",
        "sync_state",
        "last_sync_at",
        "last_sync_error",
        "kitchen_print_state",
        "kitchen_print_requested_at",
        "kitchen_print_last_attempt_at",
        "kitchen_print_sent_at",
        "kitchen_print_attempts",
        "kitchen_print_error",
        "kitchen_print_claim_key",
        "kitchen_print_claim_until",
        "cashier_receipt_state",
        "cashier_receipt_requested_at",
        "cashier_receipt_last_attempt_at",
        "cashier_receipt_sent_at",
        "cashier_receipt_attempts",
        "cashier_receipt_error",
        "cashier_receipt_claim_key",
        "cashier_receipt_claim_until",
        "cashier_receipt_manual_retry",
        *_DELIVERY_FINANCIAL_FIELDS,
    }
)

_TRANSITION_WRITE_FIELDS = frozenset(
    {
        "state",
        "external_status",
        "accepted_at",
        "preparing_at",
        "ready_at",
        "picked_up_at",
        "delivered_at",
        "rejected_at",
        "cancelled_at",
        "reject_reason_id",
        "rejection_note",
    }
)


class DeliveryOrder(models.Model):
    """Provider-neutral operational order kept independently from ``pos.order``."""

    _name = "bs.delivery.order"
    _description = "POS Delivery Order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "external_order_id"
    _order = "received_at desc, id desc"
    _check_company_auto = True

    _ALLOWED_TRANSITIONS = {
        "received": frozenset({"accepted", "rejected", "cancelled"}),
        "accepted": frozenset({"preparing", "rejected", "cancelled"}),
        "preparing": frozenset({"ready", "rejected", "cancelled"}),
        "ready": frozenset({"picked_up", "cancelled"}),
        "picked_up": frozenset({"delivered"}),
        "delivered": frozenset(),
        "rejected": frozenset(),
        "cancelled": frozenset(),
    }
    _STATE_TIMESTAMP_FIELDS = {
        "received": "received_at",
        "accepted": "accepted_at",
        "preparing": "preparing_at",
        "ready": "ready_at",
        "picked_up": "picked_up_at",
        "delivered": "delivered_at",
        "rejected": "rejected_at",
        "cancelled": "cancelled_at",
    }

    external_order_id = fields.Char(required=True, index=True, copy=False, tracking=True)
    external_reference = fields.Char(index=True, copy=False)
    external_source = fields.Char(index=True, copy=False)
    external_status = fields.Char(index=True, copy=False)

    connection_id = fields.Many2one(
        "bs.delivery.connection",
        required=True,
        index=True,
        ondelete="restrict",
        check_company=True,
        tracking=True,
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
    store_binding_id = fields.Many2one(
        "bs.delivery.store.binding",
        string="Remote Store",
        index=True,
        ondelete="restrict",
        check_company=True,
        tracking=True,
    )
    binding_id = fields.Many2one(
        "bs.delivery.order.binding",
        string="Compatibility Binding",
        index=True,
        ondelete="restrict",
        check_company=True,
        copy=False,
    )
    webhook_event_id = fields.Many2one(
        "bs.delivery.webhook.event",
        string="Source Webhook Event",
        index=True,
        ondelete="set null",
        check_company=True,
        copy=False,
    )
    incoming_request_payload = fields.Text(
        string="Incoming Request Payload",
        related="webhook_event_id.payload_preview",
        readonly=True,
        groups="bs_pos_delivery_core.group_delivery_integration_manager",
        help="Masked request body from the source webhook event, while that event is retained.",
    )
    processing_response_payload = fields.Text(
        string="Processing Response",
        compute="_compute_processing_diagnostics",
        groups="bs_pos_delivery_core.group_delivery_integration_manager",
        help="Current result of processing this delivery order inside Odoo, not the HTTP reply to the provider.",
    )
    processing_error_message = fields.Text(
        string="Processing Error",
        compute="_compute_processing_diagnostics",
        groups="bs_pos_delivery_core.group_delivery_integration_manager",
        help="Current POS, webhook, provider sync, and kitchen processing errors, when present.",
    )
    pos_order_id = fields.Many2one(
        "pos.order",
        string="POS Order",
        index=True,
        ondelete="set null",
        check_company=True,
        copy=False,
        tracking=True,
    )

    state = fields.Selection(
        DELIVERY_ORDER_STATES,
        required=True,
        default="received",
        index=True,
        copy=False,
        tracking=True,
    )
    fulfillment_type = fields.Selection(
        [
            ("delivery", "Delivery"),
            ("pickup", "Pickup"),
            ("dine_in", "Dine In"),
        ],
        default="delivery",
        required=True,
        index=True,
    )
    is_prepaid = fields.Boolean(copy=False, tracking=True)
    remote_payment_method = fields.Char(copy=False)
    contract_version = fields.Integer(default=0, readonly=True, copy=False)
    contract_snapshot_hash = fields.Char(readonly=True, copy=False, index=True)
    core_last_provider_update_at = fields.Datetime(readonly=True, copy=False, index=True)
    scheduled_at = fields.Datetime(copy=False, index=True)

    customer_name = fields.Char(copy=False)
    customer_phone = fields.Char(copy=False)
    customer_phone_masked = fields.Char(compute="_compute_customer_phone_masked")
    customer_email = fields.Char(copy=False)
    delivery_address = fields.Text(copy=False)
    customer_note = fields.Text(copy=False)

    driver_name = fields.Char(copy=False)
    driver_phone = fields.Char(copy=False)
    driver_eta_minutes = fields.Integer(copy=False)
    driver_latitude = fields.Float(digits=(10, 7), copy=False)
    driver_longitude = fields.Float(digits=(10, 7), copy=False)

    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        index=True,
        default=lambda self: self.env.company.currency_id,
    )
    items_subtotal = fields.Monetary(
        currency_field="currency_id", default=0.0, required=True
    )
    discount_amount = fields.Monetary(
        currency_field="currency_id", default=0.0, required=True
    )
    untaxed_discounts = fields.Json(default=list, copy=False)
    delivery_fee = fields.Monetary(
        currency_field="currency_id", default=0.0, required=True
    )
    service_fee = fields.Monetary(
        currency_field="currency_id", default=0.0, required=True
    )
    vat_amount = fields.Monetary(
        currency_field="currency_id", default=0.0, required=True
    )
    amount_total = fields.Monetary(
        currency_field="currency_id",
        required=True,
        default=0.0,
        tracking=True,
    )
    commission_rate = fields.Float(digits=(16, 4), default=0.0)
    commission_amount = fields.Monetary(
        currency_field="currency_id",
        required=True,
        default=0.0,
        tracking=True,
    )
    net_amount = fields.Monetary(
        currency_field="currency_id",
        required=True,
        default=0.0,
        tracking=True,
    )

    received_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        index=True,
        copy=False,
    )
    accepted_at = fields.Datetime(copy=False, index=True)
    preparing_at = fields.Datetime(copy=False, index=True)
    ready_at = fields.Datetime(copy=False, index=True)
    picked_up_at = fields.Datetime(copy=False, index=True)
    delivered_at = fields.Datetime(copy=False, index=True)
    rejected_at = fields.Datetime(copy=False, index=True)
    cancelled_at = fields.Datetime(copy=False, index=True)

    reject_reason_id = fields.Many2one(
        "bs.delivery.reject.reason",
        string="Rejection Reason",
        ondelete="restrict",
        check_company=True,
        copy=False,
    )
    rejection_note = fields.Text(copy=False)
    late_threshold_minutes = fields.Integer(default=15, required=True)
    late_deadline = fields.Datetime(
        compute="_compute_late_deadline",
        store=True,
        index=True,
        copy=False,
    )
    age_seconds = fields.Integer(compute="_compute_timing_indicators")
    is_late = fields.Boolean(
        compute="_compute_timing_indicators",
        search="_search_is_late",
    )

    line_ids = fields.One2many(
        "bs.delivery.order.line",
        "order_id",
        string="Items",
        copy=True,
    )
    payment_allocation_ids = fields.One2many(
        "bs.delivery.payment.allocation",
        "order_id",
        string="Payment Allocations",
        copy=False,
    )
    transition_ids = fields.One2many(
        "bs.delivery.order.transition",
        "order_id",
        string="State History",
        copy=False,
    )

    _external_order_unique = models.Constraint(
        "UNIQUE(connection_id, external_order_id)",
        "An external order can only exist once per delivery connection.",
    )
    _binding_unique = models.Constraint(
        "UNIQUE(binding_id)",
        "A compatibility binding can only belong to one delivery order.",
    )
    _financial_amounts_valid = models.Constraint(
        "CHECK(items_subtotal >= 0 AND discount_amount >= 0 "
        "AND delivery_fee >= 0 AND service_fee >= 0 "
        "AND small_order_fee >= 0 AND tip_amount >= 0 AND vat_amount >= 0 "
        "AND amount_total >= 0 AND commission_amount >= 0 AND net_amount >= 0 "
        "AND commission_rate >= 0 AND commission_rate <= 100)",
        "Delivery order financial amounts are invalid.",
    )
    _driver_eta_valid = models.Constraint(
        "CHECK(driver_eta_minutes IS NULL OR driver_eta_minutes >= 0)",
        "Driver ETA cannot be negative.",
    )
    _late_threshold_valid = models.Constraint(
        "CHECK(late_threshold_minutes > 0)",
        "The late-order threshold must be greater than zero.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        protected = _CANONICAL_ORDER_FIELDS.intersection(self._fields)
        internal = (
            self.env.context.get("bs_delivery_internal_update")
            is _INTERNAL_ORDER_UPDATE_TOKEN
        )
        if (
            not internal
            and not self.env.su
            and any(protected.intersection(values) for values in vals_list)
        ):
            raise AccessError(
                _(
                    "Delivery orders can only be created by a trusted ingestion "
                    "service."
                )
            )
        prepared = []
        for incoming in vals_list:
            values = self._normalize_order_values(incoming)
            connection = self.env["bs.delivery.connection"].browse(
                values.get("connection_id")
            )
            if connection and not values.get("currency_id"):
                values["currency_id"] = connection.company_id.currency_id.id
            state = values.get("state", "received")
            timestamp_field = self._STATE_TIMESTAMP_FIELDS.get(state)
            if timestamp_field:
                values.setdefault(timestamp_field, fields.Datetime.now())
            prepared.append(values)
        return super().create(prepared)

    def write(self, vals):
        values = self._normalize_order_values(vals)
        protected_fields = _CANONICAL_ORDER_FIELDS.intersection(self._fields).intersection(
            values
        )
        internal = (
            self.env.context.get("bs_delivery_internal_update")
            is _INTERNAL_ORDER_UPDATE_TOKEN
        )
        transition = (
            self.env.context.get("bs_delivery_state_transition")
            is _STATE_TRANSITION_TOKEN
            and protected_fields.issubset(_TRANSITION_WRITE_FIELDS)
        )
        correction = (
            self.env.context.get("bs_delivery_financial_correction")
            is _FINANCIAL_CORRECTION_TOKEN
            and protected_fields.issubset(_DELIVERY_FINANCIAL_FIELDS)
        )
        if (
            internal
            and _DELIVERY_FINANCIAL_FIELDS.intersection(values)
            and self.filtered(lambda order: order.state == "delivered")
        ):
            raise ValidationError(
                _(
                    "Delivered order financials and item lines are immutable, "
                    "including during provider replays."
                )
            )
        if protected_fields and not (internal or transition or correction):
            raise AccessError(
                _(
                    "Canonical delivery order fields are workflow-managed. Use a "
                    "state transition or the audited manager correction workflow."
                )
            )
        if "state" in values and self.env.context.get(
            "bs_delivery_state_transition"
        ) is not _STATE_TRANSITION_TOKEN:
            illegal = self.filtered(lambda order: order.state != values["state"])
            if illegal:
                raise ValidationError(
                    _("Use the delivery order transition service to change state.")
                )
        return super().write(values)

    def _with_delivery_internal_update(self):
        """Return an ingestion-only context that cannot be forged over RPC."""
        return self.with_context(
            bs_delivery_internal_update=_INTERNAL_ORDER_UPDATE_TOKEN
        )

    @api.model
    def _delivery_financial_field_names(self):
        return _DELIVERY_FINANCIAL_FIELDS.intersection(self._fields)

    def apply_financial_correction(self, values, reason):
        """Apply a manager-only, chatter-audited correction to a delivered order."""
        self.ensure_one()
        if not self.env.user.has_group(
            "bs_pos_delivery_core.group_delivery_manager"
        ):
            raise AccessError(
                _("Only a Delivery Manager can correct delivered financials.")
            )
        locked = self.try_lock_for_update(limit=1)
        if not locked:
            raise UserError(
                _(
                    "This delivery order is being updated by another "
                    "transaction. Retry the correction in a moment."
                )
            )
        locked.invalidate_recordset()
        self = locked
        if self.state != "delivered":
            raise UserError(_("This correction workflow is only for delivered orders."))
        reason = str(reason or "").strip()
        if not reason:
            raise ValidationError(_("A financial correction reason is required."))
        values = dict(values or {})
        allowed = _DELIVERY_FINANCIAL_FIELDS.intersection(self._fields)
        invalid = set(values) - allowed
        if not values or invalid:
            raise ValidationError(
                _("Only canonical delivery financial and item fields can be corrected: %s")
                % ", ".join(sorted(invalid or allowed))
            )
        changed = ", ".join(
            self._fields[field_name].string for field_name in sorted(values)
        )
        self.with_context(
            bs_delivery_financial_correction=_FINANCIAL_CORRECTION_TOKEN
        ).write(values)
        self.message_post(
            body=Markup(
                "<p><strong>%s</strong></p><p>%s</p><p>%s: %s</p>"
            )
            % (
                escape(_("Delivered-order financial correction")),
                escape(reason),
                escape(_("Changed fields")),
                escape(changed),
            )
        )
        return True

    @api.model
    def _normalize_order_values(self, values):
        values = dict(values)
        for field_name in (
            "external_order_id",
            "external_reference",
            "external_source",
            "external_status",
            "remote_payment_method",
        ):
            if field_name in values:
                values[field_name] = str(values[field_name] or "").strip() or False
        return values

    @api.depends(
        "state", "pos_processing_state", "pos_order_id", "pos_order_id.pos_reference",
        "pos_processing_error", "sync_state", "last_sync_error",
        "binding_id.sync_state", "binding_id.last_error",
        "webhook_event_id.state", "webhook_event_id.last_error",
        "kitchen_print_state", "kitchen_print_error",
    )
    def _compute_processing_diagnostics(self):
        for order in self:
            if order.pos_processing_state == "pending" and not order.pos_order_id:
                order.processing_response_payload = False
            else:
                order.processing_response_payload = json.dumps({
                    "delivery_state": order.state,
                    "pos_processing_state": order.pos_processing_state,
                    "pos_order_id": order.pos_order_id.id or None,
                    "pos_reference": order.pos_order_id.pos_reference or None,
                }, ensure_ascii=False, sort_keys=True)

            errors = []
            for label, message in (
                (_("POS Processing"), order.pos_processing_error
                 if order.pos_processing_state == "blocked" else False),
                (_("Incoming Webhook"), order.webhook_event_id.last_error
                 if order.webhook_event_id.state in ("retry", "failed") else False),
                (_("Provider Sync"),
                 (order.last_sync_error or order.binding_id.last_error)
                 if order.sync_state == "error" or order.binding_id.sync_state == "error"
                 else False),
                (_("Kitchen Print"), order.kitchen_print_error
                 if order.kitchen_print_state == "error" else False),
            ):
                if message:
                    errors.append("%s: %s" % (label, mask_sensitive_text(message)))
            order.processing_error_message = "\n".join(errors) or False

    @api.depends("customer_phone")
    def _compute_customer_phone_masked(self):
        for order in self:
            phone = str(order.customer_phone or "")
            if not phone:
                order.customer_phone_masked = False
            elif len(phone) <= 4:
                order.customer_phone_masked = "*" * len(phone)
            else:
                order.customer_phone_masked = "%s%s" % (
                    "*" * (len(phone) - 4),
                    phone[-4:],
                )

    @api.depends("received_at", "late_threshold_minutes")
    def _compute_late_deadline(self):
        for order in self:
            order.late_deadline = (
                order.received_at
                and order.received_at
                + timedelta(minutes=order.late_threshold_minutes or 0)
                or False
            )

    @api.depends("received_at", "state", "late_deadline")
    def _compute_timing_indicators(self):
        now = fields.Datetime.now()
        terminal_states = {"delivered", "rejected", "cancelled"}
        for order in self:
            if not order.received_at:
                order.age_seconds = 0
                order.is_late = False
                continue
            age = max(0, int((now - order.received_at).total_seconds()))
            order.age_seconds = age
            order.is_late = bool(
                order.state not in terminal_states
                and order.late_deadline
                and order.late_deadline < now
            )

    @api.model
    def _search_is_late(self, operator, value):
        if operator in ("=", "!="):
            choices = {bool(value)}
            if operator == "!=":
                choices = {True, False} - choices
        elif operator in ("in", "not in"):
            raw_values = value if isinstance(value, (list, tuple, set)) else [value]
            choices = {bool(item) for item in raw_values}
            if operator == "not in":
                choices = {True, False} - choices
        else:
            raise NotImplementedError(
                "is_late only supports boolean equality and membership operators"
            )
        if choices == {True, False}:
            return []
        if not choices:
            return [("id", "=", 0)]
        want_late = True in choices
        now = fields.Datetime.now()
        if want_late:
            return [
                ("state", "not in", ["delivered", "rejected", "cancelled"]),
                ("late_deadline", "!=", False),
                ("late_deadline", "<", now),
            ]
        return [
            "|",
            ("state", "in", ["delivered", "rejected", "cancelled"]),
            "|",
            ("late_deadline", "=", False),
            ("late_deadline", ">=", now),
        ]

    @api.constrains("external_order_id")
    def _check_external_order_id(self):
        for order in self:
            if not (order.external_order_id or "").strip():
                raise ValidationError(_("External Order ID cannot be empty."))

    @api.constrains(
        "connection_id",
        "store_binding_id",
        "binding_id",
        "webhook_event_id",
        "pos_order_id",
        "reject_reason_id",
    )
    def _check_related_record_scope(self):
        for order in self:
            if (
                order.store_binding_id
                and order.store_binding_id.connection_id != order.connection_id
            ):
                raise ValidationError(
                    _("The remote store and delivery order must use the same connection.")
                )
            if order.binding_id:
                if order.binding_id.connection_id != order.connection_id:
                    raise ValidationError(
                        _("The compatibility binding belongs to another connection.")
                    )
                if order.binding_id.external_order_id != order.external_order_id:
                    raise ValidationError(
                        _("The compatibility binding uses a different external order ID.")
                    )
                if (
                    order.binding_id.pos_order_id
                    and order.pos_order_id
                    and order.binding_id.pos_order_id != order.pos_order_id
                ):
                    raise ValidationError(
                        _("The compatibility binding points to another POS order.")
                    )
            if (
                order.webhook_event_id
                and order.webhook_event_id.connection_id != order.connection_id
            ):
                raise ValidationError(
                    _("The source webhook event belongs to another connection.")
                )
            if order.pos_order_id and order.pos_order_id.company_id != order.company_id:
                raise ValidationError(
                    _("The POS order must belong to the delivery order company.")
                )
            order._check_reject_reason_scope(order.reject_reason_id)

    def _check_reject_reason_scope(self, reason):
        self.ensure_one()
        if not reason:
            return
        if reason.company_id and reason.company_id != self.company_id:
            raise ValidationError(
                _("The rejection reason must be shared or belong to the same company.")
            )
        if reason.provider_id and reason.provider_id != self.provider_id:
            raise ValidationError(
                _("The rejection reason belongs to another delivery provider.")
            )

    def transition_to(
        self,
        new_state,
        *,
        idempotency_key=None,
        source="manual",
        expected_state=None,
        reject_reason_id=None,
        reason=None,
        external_status=None,
    ):
        """Atomically transition one order and return its immutable audit record."""
        self.ensure_one()
        if not self.exists():
            raise UserError(_("The delivery order no longer exists."))
        new_state = str(new_state or "").strip()
        valid_states = {value for value, _label in DELIVERY_ORDER_STATES}
        if new_state not in valid_states:
            raise ValidationError(_("Unknown delivery order state: %s") % new_state)
        valid_sources = {value for value, _label in DELIVERY_TRANSITION_SOURCES}
        if source not in valid_sources:
            raise ValidationError(_("Unknown delivery transition source: %s") % source)

        reason_record = self.env["bs.delivery.reject.reason"]
        if reject_reason_id:
            if getattr(reject_reason_id, "_name", None) == "bs.delivery.reject.reason":
                reason_record = reject_reason_id.exists()
            else:
                reason_record = self.env["bs.delivery.reject.reason"].browse(
                    reject_reason_id
                ).exists()
            if not reason_record:
                raise ValidationError(_("The rejection reason no longer exists."))
            reason_record.ensure_one()

        raw_key = str(idempotency_key or uuid.uuid4())
        normalized_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        # Transition rows are workflow-managed audit records.  Operators only
        # need write access to the order itself; creating the immutable audit
        # row is deliberately elevated after that write has passed ACL/rules.
        actor_id = self.env.user.id
        if self.env.context.get("bs_delivery_actor_token") is _ACTOR_OVERRIDE_TOKEN:
            actor_id = int(
                self.env.context.get("bs_delivery_actor_id") or actor_id
            )
        Transition = self.env["bs.delivery.order.transition"].sudo()

        self.env.cr.execute(
            "SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset()

        existing = Transition.search(
            [
                ("order_id", "=", self.id),
                ("idempotency_key", "=", normalized_key),
            ],
            limit=1,
        )
        if existing:
            if existing.to_state != new_state:
                raise ValidationError(
                    _("The transition idempotency key was already used for another state.")
                )
            return existing

        current_state = self.state
        if expected_state and current_state != expected_state:
            raise ValidationError(
                _(
                    "Delivery order state changed from %(expected)s to %(actual)s.",
                    expected=expected_state,
                    actual=current_state,
                )
            )
        if (
            new_state != current_state
            and new_state not in self._ALLOWED_TRANSITIONS[current_state]
        ):
            raise ValidationError(
                _(
                    "Delivery order cannot move from %(source)s to %(target)s.",
                    source=current_state,
                    target=new_state,
                )
            )
        reason_text = str(reason or "").strip() or False
        if new_state == "rejected" and not reason_record and not reason_text:
            raise ValidationError(_("A rejection reason is required."))
        if reason_record.requires_note and not reason_text:
            raise ValidationError(
                _("The selected rejection reason requires an operator note.")
            )
        self._check_reject_reason_scope(reason_record)

        now = fields.Datetime.now()
        if new_state != current_state:
            self._before_delivery_state_transition(new_state, source)
            order_values = {"state": new_state}
            timestamp_field = self._STATE_TIMESTAMP_FIELDS[new_state]
            if not self[timestamp_field]:
                order_values[timestamp_field] = now
            if new_state == "rejected":
                order_values.update(
                    {
                        "reject_reason_id": reason_record.id or False,
                        "rejection_note": reason_text,
                    }
                )
            if external_status is not None:
                order_values["external_status"] = external_status
            self.with_context(
                bs_delivery_state_transition=_STATE_TRANSITION_TOKEN
            ).write(order_values)
        elif external_status is not None and external_status != self.external_status:
            self.with_context(
                bs_delivery_state_transition=_STATE_TRANSITION_TOKEN
            ).write({"external_status": external_status})

        transition = Transition.create(
            {
                "order_id": self.id,
                "from_state": current_state,
                "to_state": new_state,
                "source": source,
                "idempotency_key": normalized_key,
                "actor_id": actor_id,
                "reject_reason_id": reason_record.id or False,
                "reason": reason_text,
                "external_status": str(external_status or "").strip() or False,
                "transitioned_at": now,
                "is_noop": new_state == current_state,
            }
        )
        self._after_delivery_state_transition(transition)
        return transition

    def _before_delivery_state_transition(self, new_state, source):
        """Validate local side effects before changing the canonical state."""
        self.ensure_one()

    def _after_delivery_state_transition(self, transition):
        """Connector/outbox hook invoked inside the transition transaction."""
        self.ensure_one()
        return True

    def _with_delivery_actor(self, user):
        """Carry the real actor through an explicitly scoped sudo operation."""
        user.ensure_one()
        return self.with_context(
            bs_delivery_actor_id=user.id,
            bs_delivery_actor_token=_ACTOR_OVERRIDE_TOKEN,
        )

    def create_pos_order(self):
        """Create at most one POS order, delegating values to the workflow hook."""
        self.ensure_one()
        self.env.cr.execute(
            "SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset(["pos_order_id"])
        if self.pos_order_id:
            return self.pos_order_id
        # Callers may catch a configuration/financial error and keep the inbound
        # event. Roll back every POS line, payment and link from this attempt.
        with self.env.cr.savepoint():
            return self._create_pos_order_hook()

    def _create_pos_order_hook(self):
        """Override in a workflow addon; Core deliberately creates nothing."""
        self.ensure_one()
        raise UserError(
            _("POS order creation is not implemented by the delivery core module.")
        )


class DeliveryOrderLine(models.Model):
    _name = "bs.delivery.order.line"
    _description = "POS Delivery Order Line"
    _order = "order_id, sequence, id"
    _check_company_auto = True

    order_id = fields.Many2one(
        "bs.delivery.order",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
    )
    company_id = fields.Many2one(
        related="order_id.company_id",
        store=True,
        index=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="order_id.currency_id",
        store=True,
        readonly=True,
    )
    sequence = fields.Integer(default=10)
    external_line_id = fields.Char(index=True, copy=False)
    external_product_id = fields.Char(index=True, copy=False)
    external_variant_id = fields.Char(index=True, copy=False)
    external_sku = fields.Char(string="Platform SKU", index=True, copy=False)
    name = fields.Char(string="Item Name", required=True)
    name_ar = fields.Char(string="Arabic Item Name")
    modifier_text = fields.Text(string="Platform Modifiers", copy=False)
    product_id = fields.Many2one(
        "product.product",
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    quantity = fields.Float(
        required=True,
        default=1.0,
        digits="Product Unit of Measure",
    )
    price_unit = fields.Monetary(currency_field="currency_id", default=0.0)
    discount_amount = fields.Monetary(currency_field="currency_id", default=0.0)
    vat_amount = fields.Monetary(currency_field="currency_id", default=0.0)
    price_subtotal = fields.Monetary(currency_field="currency_id", default=0.0)
    customer_note = fields.Text(copy=False)
    is_modifier = fields.Boolean(default=False)
    parent_line_id = fields.Many2one(
        "bs.delivery.order.line",
        string="Parent Item",
        index=True,
        ondelete="cascade",
        check_company=True,
    )
    modifier_line_ids = fields.One2many(
        "bs.delivery.order.line",
        "parent_line_id",
        string="Modifiers",
    )

    _line_amounts_valid = models.Constraint(
        "CHECK(quantity > 0 AND price_unit >= 0 AND discount_amount >= 0 "
        "AND vat_amount >= 0 AND price_subtotal >= 0)",
        "Delivery order line quantity and amounts must be valid.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for incoming in vals_list:
            values = dict(incoming)
            if not values.get("order_id") and values.get("parent_line_id"):
                parent = self.browse(values["parent_line_id"]).exists()
                if parent:
                    values["order_id"] = parent.order_id.id
            prepared.append(values)
        vals_list = prepared
        internal = (
            self.env.context.get("bs_delivery_internal_update")
            is _INTERNAL_ORDER_UPDATE_TOKEN
        )
        orders = self.env["bs.delivery.order"].browse(
            {
                values.get("order_id")
                for values in vals_list
                if values.get("order_id")
            }
        )
        if internal and orders.filtered(lambda order: order.state == "delivered"):
            raise ValidationError(
                _("Delivered order item lines require an audited manager correction.")
            )
        trusted = (
            self.env.context.get("bs_delivery_financial_correction")
            is _FINANCIAL_CORRECTION_TOKEN
            or self.env.context.get("bs_delivery_internal_update")
            is _INTERNAL_ORDER_UPDATE_TOKEN
            or self.env.su
        )
        if not trusted:
            raise AccessError(_("Delivery order item lines are workflow-managed."))
        return super().create(vals_list)

    def write(self, vals):
        internal = (
            self.env.context.get("bs_delivery_internal_update")
            is _INTERNAL_ORDER_UPDATE_TOKEN
        )
        orders = self.mapped("order_id")
        if vals.get("order_id"):
            orders |= self.env["bs.delivery.order"].browse(vals["order_id"])
        if internal and orders.filtered(lambda order: order.state == "delivered"):
            raise ValidationError(
                _("Delivered order item lines require an audited manager correction.")
            )
        trusted = (
            self.env.context.get("bs_delivery_financial_correction")
            is _FINANCIAL_CORRECTION_TOKEN
            or self.env.context.get("bs_delivery_internal_update")
            is _INTERNAL_ORDER_UPDATE_TOKEN
            or self.env.su
        )
        if not trusted:
            raise AccessError(_("Delivery order item lines are workflow-managed."))
        return super().write(vals)

    def unlink(self):
        internal = (
            self.env.context.get("bs_delivery_internal_update")
            is _INTERNAL_ORDER_UPDATE_TOKEN
        )
        if internal and self.mapped("order_id").filtered(
            lambda order: order.state == "delivered"
        ):
            raise ValidationError(
                _("Delivered order item lines require an audited manager correction.")
            )
        trusted = (
            self.env.context.get("bs_delivery_financial_correction")
            is _FINANCIAL_CORRECTION_TOKEN
            or self.env.context.get("bs_delivery_internal_update")
            is _INTERNAL_ORDER_UPDATE_TOKEN
            or self.env.su
        )
        if not trusted:
            raise AccessError(_("Delivery order item lines are workflow-managed."))
        return super().unlink()

    @api.constrains("order_id", "product_id", "parent_line_id")
    def _check_line_scope(self):
        for line in self:
            if (
                line.product_id
                and line.product_id.company_id
                and line.product_id.company_id != line.company_id
            ):
                raise ValidationError(
                    _("The mapped product belongs to another company.")
                )
            if line.parent_line_id and line.parent_line_id.order_id != line.order_id:
                raise ValidationError(
                    _("A modifier and its parent item must belong to the same order.")
                )
            if line.parent_line_id and not line.is_modifier:
                raise ValidationError(
                    _("A line with a parent item must be marked as a modifier.")
                )


class DeliveryOrderTransition(models.Model):
    _name = "bs.delivery.order.transition"
    _description = "POS Delivery Order State Transition"
    _order = "transitioned_at desc, id desc"
    _check_company_auto = True

    order_id = fields.Many2one(
        "bs.delivery.order",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
    )
    company_id = fields.Many2one(
        related="order_id.company_id",
        store=True,
        index=True,
        readonly=True,
    )
    from_state = fields.Selection(DELIVERY_ORDER_STATES, required=True, index=True)
    to_state = fields.Selection(DELIVERY_ORDER_STATES, required=True, index=True)
    source = fields.Selection(
        DELIVERY_TRANSITION_SOURCES,
        required=True,
        default="manual",
        index=True,
    )
    idempotency_key = fields.Char(required=True, index=True, copy=False)
    actor_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
        ondelete="restrict",
    )
    reject_reason_id = fields.Many2one(
        "bs.delivery.reject.reason",
        ondelete="restrict",
        check_company=True,
    )
    reason = fields.Text(copy=False)
    external_status = fields.Char(index=True, copy=False)
    transitioned_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        index=True,
        copy=False,
    )
    is_noop = fields.Boolean(default=False, copy=False)

    _idempotency_unique = models.Constraint(
        "UNIQUE(order_id, idempotency_key)",
        "A delivery order transition idempotency key can only be used once.",
    )

    @api.constrains("order_id", "reject_reason_id")
    def _check_transition_reason_scope(self):
        for transition in self:
            transition.order_id._check_reject_reason_scope(
                transition.reject_reason_id
            )
