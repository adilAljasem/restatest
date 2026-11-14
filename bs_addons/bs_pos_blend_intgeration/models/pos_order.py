import re

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


BLEND_STATUSES = [
    ("pending", "Pending"),
    ("scheduled", "Scheduled"),
    ("accepted", "Accepted"),
    ("preparing", "Preparing"),
    ("prepared", "Prepared"),
    ("ready_to_pick_up", "Ready for Pickup"),
    ("rejected", "Rejected"),
    ("cancelled", "Cancelled"),
    ("time_out", "Timed Out"),
    ("assigned", "Driver Assigned"),
    ("at_pick_up", "Driver at Pickup"),
    ("on_the_way", "On the Way"),
    ("delivered", "Delivered"),
    ("completed", "Completed"),
]


class PosOrder(models.Model):
    _inherit = "pos.order"

    blend_order_id = fields.Char(string="Blend Order ID", copy=False, index=True, readonly=True)
    blend_partner_reference = fields.Char(
        string="Blend Partner Reference",
        copy=False,
        index=True,
        readonly=True,
    )
    blend_order_type = fields.Char(
        string="Blend Order Type", copy=False, readonly=True
    )
    blend_aggregator = fields.Char(
        string="Blend Aggregator", copy=False, readonly=True
    )
    blend_aggregator_order_id = fields.Char(
        string="Aggregator Order ID", copy=False, index=True, readonly=True
    )
    blend_payload = fields.Json(string="Blend Payload", copy=False, readonly=True)
    blend_schedule_at = fields.Char(string="Scheduled For (Blend local time)", copy=False, readonly=True, index=True)
    blend_scheduled_prepared_at = fields.Datetime(string="Prepared At", copy=False, readonly=True)
    blend_scheduled_prepared_by = fields.Many2one("res.users", string="Prepared By", copy=False, readonly=True)
    blend_discount_amount = fields.Monetary(
        string="Blend Discount",
        currency_field="currency_id",
        copy=False,
        readonly=True,
    )
    blend_status = fields.Selection(BLEND_STATUSES, string="Blend Status", copy=False, tracking=True)
    blend_delivery_status = fields.Selection(
        BLEND_STATUSES,
        string="Status to Blend",
        copy=False,
    )
    blend_is_inbound = fields.Boolean(
        string="From Blend", compute="_compute_blend_is_inbound", store=True,
    )
    blend_can_decide = fields.Boolean(compute="_compute_blend_decision_availability")
    blend_decision_user_id = fields.Many2one("res.users", string="Decision By", readonly=True, copy=False)
    blend_decision_at = fields.Datetime(string="Decision Time", readonly=True, copy=False)
    blend_decision_source = fields.Selection(
        [("manual", "Manual"), ("automatic", "Automatic")],
        string="Decision Source", readonly=True, copy=False,
    )
    blend_rejection_reason = fields.Text(string="Rejection Reason", readonly=True, copy=False)
    blend_decision_log_id = fields.Many2one(
        "blend.integration.log", string="Decision Delivery Log", readonly=True,
        copy=False, ondelete="set null",
    )
    blend_accepted_delivery_log_id = fields.Many2one(
        "blend.integration.log", string="Delivered Acceptance Log", readonly=True,
        copy=False, ondelete="set null", groups="base.group_system",
        help="Confirmed ACCEPTED delivery. Also serializes duplicate queue jobs under snapshot isolation.",
    )
    blend_status_sync_revision = fields.Integer(
        readonly=True, copy=False,
        help="Internal concurrency fence for queued and acknowledged order statuses; never sent to Blend.",
    )
    blend_decision_sync_state = fields.Selection(
        related="blend_decision_log_id.state", string="Decision Delivery to Blend",
        readonly=True, compute_sudo=True,
        help="Only the queue state is exposed. Success confirms delivery; pending/retry/failed does not.",
    )
    blend_car_color = fields.Char(string="Car Color", copy=False)
    blend_car_plate_number = fields.Char(string="Car Plate Number", copy=False)
    blend_car_plate_letters = fields.Char(string="Car Plate Letters", copy=False)
    blend_car_brand = fields.Char(string="Car Brand", copy=False)

    _blend_partner_reference_unique = models.Constraint(
        "unique (config_id, blend_partner_reference)",
        "The Blend partner reference must be unique inside a POS branch.",
    )

    @api.depends("blend_partner_reference")
    def _compute_blend_is_inbound(self):
        for order in self:
            order.blend_is_inbound = bool(order.blend_partner_reference)

    @api.depends("blend_is_inbound", "blend_status", "state", "amount_paid", "payment_ids", "config_id.blend_enabled")
    def _compute_blend_decision_availability(self):
        for order in self:
            order.blend_can_decide = bool(
                order.blend_is_inbound and order.config_id.blend_enabled
                and order.blend_status in ("pending", "scheduled") and order.state == "draft"
                and not order.payment_ids and not order.amount_paid
            )

    def _blend_check_decision_access(self):
        self.ensure_one()
        if not (self.env.user.has_group("point_of_sale.group_pos_user")
                or self.env.user.has_group("base.group_system")):
            raise AccessError(_("Only Point of Sale users may accept or reject Blend orders."))
        self.check_access("write")
        if self.company_id not in self.env.companies:
            raise AccessError(_("This order belongs to a company you have not selected."))

    def action_blend_mark_scheduled_prepared(self):
        for order in self:
            order._blend_check_decision_access()
            with order.env.cr.savepoint():
                order.flush_recordset()
                order.lock_for_update()
                order.invalidate_recordset(["blend_schedule_at", "blend_scheduled_prepared_at", "blend_status", "state"])
                if not order.blend_is_inbound or not order.blend_schedule_at:
                    raise UserError(_("Only scheduled Blend orders can be marked prepared."))
                if order.blend_scheduled_prepared_at:
                    continue
                if (order.state == "cancel" or order.blend_status in ("cancelled", "rejected", "time_out")
                        or (order.state == "draft" and order.blend_status in ("scheduled", "pending"))):
                    raise UserError(_("Accept the scheduled order before marking it prepared."))
                order.write({
                    "blend_scheduled_prepared_at": fields.Datetime.now(),
                    "blend_scheduled_prepared_by": self.env.uid,
                })
        return True

    # ------------------------------------------------------------------ Resta kitchen screen (resta_kds)
    def _resta_kds_targets(self, paid_now=False):
        if self.blend_partner_reference and not self.env.context.get("blend_kitchen_send"):
            return None  # Blend decides when its orders reach the kitchen (_blend_send_to_preparation)
        return super()._resta_kds_targets(paid_now)

    def _resta_kds_header(self):
        vals = super()._resta_kds_header()
        if self.blend_schedule_at:
            # Blend gives the time already in the branch's local time
            info = _("مجدول %s", self.blend_schedule_at[:16])
            vals["info"] = " · ".join(x for x in (info, vals.get("info")) if x)
        return vals

    def _blend_validate_pending_decision(self, decision=None):
        self.ensure_one()
        if not self.blend_is_inbound:
            raise UserError(_("Only orders received from Blend can be accepted or rejected here."))
        if not self.blend_can_decide and not (decision == "accepted" and self.blend_can_finalize):
            raise UserError(_("Only pending, unpaid Blend orders can be accepted or rejected. Refresh the order to see its current status."))
        self.config_id._blend_check_decision_settings()

    def _blend_apply_manual_decision(self, decision, reason=""):
        self._blend_check_decision_access()
        if decision not in ("accepted", "rejected"):
            raise ValidationError(_("Invalid Blend order decision."))
        reason = (reason or "").strip()
        if decision == "rejected" and not reason:
            raise ValidationError(_("Please enter a rejection reason."))
        if len(reason) > 1000:
            raise ValidationError(_("The rejection reason must not exceed 1000 characters."))
        # Recheck payment and decision state under Odoo's native row lock.
        # A conflicting decision must retry/refresh, never overwrite this one.
        with self.env.cr.savepoint():
            self.flush_recordset()
            self.lock_for_update()
            self.invalidate_recordset([
                "state", "blend_status", "amount_paid", "payment_ids",
                "blend_partner_reference", "blend_decision_at", "blend_decision_log_id",
                "blend_is_inbound", "blend_can_decide",
                "blend_can_finalize", "blend_financial_state",
            ])
            if (self.blend_is_inbound and self.blend_status == decision
                    and self.blend_decision_at and self.blend_decision_log_id):
                return True  # replay of the same completed decision
            if (decision == "accepted" and self.blend_is_inbound and self.blend_financial_required
                    and self.blend_financial_state == "done" and self.blend_decision_at):
                return True
            self._blend_validate_pending_decision(decision=decision)
            if decision == "accepted" and self.blend_financial_required:
                # Authorization was checked without sudo above, on this exact
                # company/order. Native accounting then runs in a narrow scope.
                return self.sudo().with_company(self.company_id)._blend_accept_with_accounting(
                    "manual", user_id=self.env.uid,
                )
            self.with_context(blend_skip_status_sync=True).write({
                "blend_status": decision,
                "blend_decision_user_id": self.env.uid,
                "blend_decision_at": fields.Datetime.now(),
                "blend_decision_source": "manual",
                "blend_rejection_reason": reason if decision == "rejected" else False,
            })
            if decision == "rejected":
                # The exact pending order and caller were validated and locked
                # above. Financial snapshots cannot be changed through raw RPC.
                cancel_order = self.sudo().with_company(self.company_id) if self.blend_financial_required else self
                cancel_order.with_context(blend_skip_status_sync=True).action_pos_order_cancel()
            self._blend_enqueue_status(decision)
            if decision == "accepted":
                self._blend_send_to_preparation()
            message = _("Blend order accepted manually. Delivery notification queued.")
            if decision == "rejected":
                message = _("Blend order rejected manually and cancelled. Delivery notification queued.")
            if self.blend_recovered_fulfilled:
                message = _("Recovered Blend order updated locally. No kitchen or Blend status notification was sent.")
            self.message_post(body=Markup("<p>%s</p><p>%s</p>") % (message, reason))
            self.config_id.notify_synchronisation(self.session_id.id, 0)
            self.config_id._blend_notify({
                "kind": "order", "branch_id": self.config_id.blend_branch_id,
                "order_id": self.uuid, "status": decision, "pos_order_id": self.id,
            })
        return True

    def action_blend_accept_order(self):
        if not self._blend_apply_manual_decision("accepted"):
            return {
                "type": "ir.actions.client", "tag": "display_notification",
                "params": {"title": _("Blend Accounting Failed"),
                           "message": self.blend_financial_error, "type": "danger", "sticky": True,
                           "next": {"type": "ir.actions.client", "tag": "reload"}},
            }
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_blend_reject_order(self):
        self._blend_check_decision_access()
        self._blend_validate_pending_decision()
        return {
            "type": "ir.actions.act_window",
            "name": _("Reject Blend Order"),
            "res_model": "blend.order.reject.wizard", "view_mode": "form", "target": "new",
            "context": {"default_order_id": self.id},
        }

    def _process_saved_order(self, draft):
        result = super(PosOrder, self.with_context(blend_skip_status_sync=True))._process_saved_order(
            draft
        )
        if (not self.env.context.get("blend_financial_processing")
                and not draft and self.config_id.blend_enabled and self.amount_total >= 0):
            self._blend_enqueue_order_created()
            if self.config_id.blend_outbound_webhook_url or self.config_id.blend_schema == "pos":
                self._blend_enqueue_status("accepted")
        return result

    def write(self, vals):
        previous_states = {order.id: order.state for order in self} if "state" in vals else {}
        result = super().write(vals)
        if previous_states and not self.env.context.get("blend_skip_status_sync"):
            for order in self.filtered(lambda record: record.config_id.blend_enabled):
                if previous_states.get(order.id) == order.state:
                    continue
                status = False
                if order.state == "cancel":
                    status = "cancelled"
                elif (order.config_id.blend_outbound_webhook_url or order.config_id.blend_schema == "pos") and order.state == "paid":
                    status = "accepted"
                if status:
                    order._blend_enqueue_status(status)
        return result

    def _blend_payment_method(self):
        names = list(dict.fromkeys(self.payment_ids.mapped("payment_method_id.name")))
        return ", ".join(names) or "other"

    def _blend_order_type(self):
        candidates = []
        if "pos_order_type_name" in self._fields:
            candidates.append(self["pos_order_type_name"] or "")
        candidates.append(self.preset_id.name or "")
        text = " ".join(candidates).strip().lower()
        if any(token in text for token in ("delivery", "توصيل")):
            return "DELIVERY"
        if any(
            token in text
            for token in ("pickup", "pick-up", "takeaway", "take away", "سفري", "استلام")
        ):
            return "PICK-UP"
        if getattr(self, "table_id", False):
            return "DINE-IN"
        return "DINE-IN"

    def _blend_customer_payload(self):
        partner = self.partner_id
        if not partner:
            return None
        address = ", ".join(
            value
            for value in (
                partner.street,
                partner.street2,
                partner.city,
                partner.state_id.name,
                partner.country_id.name,
            )
            if value
        ) or None
        phone = re.sub(
            r"[\s()\-]",
            "",
            self.mobile
            or getattr(partner, "mobile", False)
            or partner.phone
            or "",
        )
        if not re.fullmatch(r"\+[1-9]\d{6,14}", phone):
            raise ValidationError(
                _("Customer phone must include a valid country code, for example +966500000.")
            )
        return {
            "name": partner.name or "",
            "phone": phone,
            "address": address,
        }

    def _blend_car_payload(self):
        values = {
            "color": self.blend_car_color or "",
            "plate_number": self.blend_car_plate_number or "",
            "plate_letters": self.blend_car_plate_letters or "",
            "car_brand": self.blend_car_brand or "",
        }
        return values if any(values.values()) else None

    def _blend_product_external_id(self, product_template):
        if product_template.blend_config_id and product_template.blend_config_id != self.config_id:
            raise ValidationError(
                _("Product '%s' belongs to another Blend branch.")
                % product_template.display_name
            )
        external_id = (
            product_template.blend_external_id or product_template.default_code or ""
        ).strip()
        if not external_id:
            raise ValidationError(
                _("Product '%s' has no Blend ID.") % product_template.display_name
            )
        return external_id

    def _blend_option_external_id(self, product_template):
        if product_template.blend_config_id and product_template.blend_config_id != self.config_id:
            raise ValidationError(
                _("Modifier '%s' belongs to another Blend branch.")
                % product_template.display_name
            )
        external_id = (
            product_template.blend_external_id or product_template.default_code or ""
        ).strip()
        if not external_id:
            raise ValidationError(
                _("Modifier '%s' has no Blend ID.") % product_template.display_name
            )
        return external_id

    def _blend_build_items(self):
        self.ensure_one()
        normal_lines = self.lines.filtered(lambda line: not getattr(line, "parent_line_id", False))
        modifier_lines = self.lines - normal_lines
        items = []
        for line in normal_lines:
            parent_keys = {str(line.id), str(line.uuid or "")}
            options = []
            for option_line in modifier_lines.filtered(
                lambda candidate: str(candidate.parent_line_id or "") in parent_keys
            ):
                selection_quantity = (
                    option_line.modifier_selection_qty
                    if "modifier_selection_qty" in option_line._fields
                    and option_line.modifier_selection_qty
                    else abs(option_line.qty)
                )
                options.append(
                    {
                        "id": self._blend_option_external_id(option_line.product_id.product_tmpl_id),
                        "price": option_line.price_unit,
                        "quantity": selection_quantity,
                    }
                )
            items.append(
                {
                    "id": self._blend_product_external_id(line.product_id.product_tmpl_id),
                    "quantity": abs(line.qty),
                    "price": line.price_unit,
                    "note": line.customer_note or "",
                    "options": options,
                }
            )
        if not items:
            raise ValidationError(_("The POS order has no Blend order items."))
        return items

    def _blend_build_order_created_payload(self):
        self.ensure_one()
        currency = self.currency_id
        gross_subtotal = sum(abs(line.qty) * line.price_unit for line in self.lines)
        line_discount = sum(
            abs(line.qty) * line.price_unit * (line.discount or 0.0) / 100.0
            for line in self.lines
        )
        discount = self.blend_discount_amount or line_discount
        schedule_date = fields.Datetime.to_string(self.preset_time) if self.preset_time else None
        return {
            "event": "order_created",
            "event_time": fields.Datetime.to_string(self.date_order or fields.Datetime.now()),
            "branch_id": self.config_id.blend_branch_id,
            "payload": {
                "id": self.uuid or self.pos_reference or str(self.id),
                "payment_method": self._blend_payment_method(),
                "order_type": self._blend_order_type(),
                "note": self.general_customer_note or "",
                "subtotal": currency.round(gross_subtotal),
                "tax": currency.round(self.amount_tax),
                "discount": currency.round(discount),
                "total": currency.round(self.amount_total),
                "is_schedule": 1 if schedule_date else 0,
                "schedule_date": schedule_date,
                "car_data": self._blend_car_payload(),
                "items": self._blend_build_items(),
                "customer": self._blend_customer_payload(),
            },
        }

    def _blend_enqueue_order_created(self):
        log_model = self.env["blend.integration.log"]
        for order in self.filtered(lambda record: record.config_id._blend_legacy_order_creation_active()):
            key = "order_created:%s:%s" % (order.config_id.id, order.uuid or order.id)
            try:
                payload = order._blend_build_order_created_payload()
                order.blend_payload = payload
                log_model.enqueue(
                    order.config_id,
                    "order_created",
                    payload,
                    order=order,
                    idempotency_key=key,
                    external_reference=payload["payload"]["id"],
                )
            except Exception as error:
                log = log_model.enqueue(
                    order.config_id,
                    "order_created",
                    {"_build_required": True, "order_id": order.id},
                    order=order,
                    idempotency_key=key,
                    external_reference=order.uuid or str(order.id),
                )
                log.write({"state": "failed", "error_message": str(error)[:2000]})
        return True

    def _blend_status_payload(self, status):
        self.ensure_one()
        event_time = fields.Datetime.to_string(fields.Datetime.now())
        order_reference = self.uuid or self.pos_reference or str(self.id)
        if self.config_id.blend_outbound_webhook_url or self.config_id.blend_schema == "pos":
            allowed = {"pending", "accepted", "preparing", "cancelled", "rejected", "time_out"}
            if status not in allowed:
                raise ValidationError(
                    _("Status '%s' is not supported by the configured Blend POS schema.") % status
                )
            status_map = {
                "cancelled": "CANCELED",
                "accepted": "ACCEPTED",
                "preparing": "PREPARING",
                "rejected": "REJECTED",
                "time_out": "TIME_OUT",
            }
            return {
                "brand_id": self.config_id.blend_brand_id or "",
                "event_time": event_time,
                "event": "order.updated",
                "branch_id": self.config_id.blend_branch_id,
                "order_id": order_reference,
                "status": status_map.get(status, str(status).upper()),
            }
        allowed = {"assigned", "at_pick_up", "on_the_way", "cancelled", "delivered"}
        if status not in allowed:
            raise ValidationError(
                _("Status '%s' is not supported by the configured Blend primary schema.")
                % status
            )
        return {
            "event": "order_updated",
            "event_time": event_time,
            "branch_id": self.config_id.blend_branch_id,
            "payload": {
                "order_id": order_reference,
                "status": status,
            },
        }

    def _blend_send_to_preparation(self):
        """Use native quantity tracking and category routing inside acceptance."""
        self.ensure_one()
        if self.blend_partner_reference and self.state != "cancel":
            self.with_context(blend_kitchen_send=True, resta_kds_fire=True,
                              resta_kds_raise=True)._resta_kds_dispatch(paid_now=True)
            self._blend_queue_kitchen_print()

    def _blend_auto_accept(self):
        """Run only for newly created inbound orders, inside their savepoint.

        The durable queue is committed together with the draft order; no HTTP
        request is performed here. Replays of create_order return before this step.
        """
        for order in self:
            if (
                not order.config_id.blend_enabled
                or not order.config_id.blend_auto_accept
                or not order.blend_partner_reference
                or order.state != "draft"
                or (order.blend_status not in ("pending", "scheduled") and not (
                    order.blend_financial_required
                    and order.blend_status in (
                        "accepted", "preparing", "assigned", "ready_to_pick_up",
                        "at_pick_up", "on_the_way", "completed",
                    )
                ))
            ):
                continue
            order.config_id._check_blend_auto_accept_settings()
            if order.blend_financial_required:
                order.flush_recordset()
                order.lock_for_update()
                order.invalidate_recordset(["state", "blend_status"])
                if order.state != "draft" or order.blend_status not in (
                    "pending", "scheduled", "accepted", "preparing", "assigned", "ready_to_pick_up",
                    "at_pick_up", "on_the_way", "completed",
                ):
                    continue
                order.sudo().with_company(order.company_id)._blend_accept_with_accounting("automatic")
                continue
            order.with_context(blend_skip_status_sync=True).write({
                "blend_status": "accepted",
                "blend_decision_at": fields.Datetime.now(),
                "blend_decision_source": "automatic",
            })
            order._blend_enqueue_status("accepted")
            order._blend_send_to_preparation()
            order.message_post(body=_("Blend order accepted automatically. Delivery notification queued."))
        return True

    def _blend_acceptance_obsolete(self):
        """Payment/sync callbacks must not move an order back to ACCEPTED."""
        self.ensure_one()
        return self.state == "cancel" or self.blend_status not in (False, "pending", "accepted")

    def _blend_enqueue_status(self, status):
        for order in self:
            if not order.config_id.blend_enabled or not order.blend_partner_reference:
                continue
            if status == "accepted" and order.blend_financial_required and order.blend_financial_state != "done":
                continue  # Neither payment callbacks nor remote echoes may bypass financial acceptance.
            order.flush_recordset()
            order.lock_for_update(allow_referencing=True)
            order.invalidate_recordset([
                "state", "blend_status", "blend_partner_reference", "blend_status_sync_revision",
            ])
            if not order.blend_partner_reference:
                continue
            if status == "accepted" and order._blend_acceptance_obsolete():
                continue
            payload = order._blend_status_payload(status)
            event = payload["event"]
            # Queue IDs, not second-resolution timestamps, identify successive
            # intents. Inspect only this order/branch/company, including old jobs.
            latest = self.env["blend.integration.log"].sudo().search([
                ("order_id", "=", order.id), ("config_id", "=", order.config_id.id),
                ("company_id", "=", order.company_id.id), ("direction", "=", "outbound"),
                ("event", "in", ["order.updated", "order_updated"]),
            ], order="id desc", limit=1)
            if (status not in ("accepted", "rejected") and latest
                    and latest.event == event and latest._order_status() == status
                    and latest.state != "superseded"):
                # Repeated callbacks keep the original payload, retry history and
                # acknowledgement. A failed job still needs explicit retry.
                continue
            # Acceptance/rejection is stable across later payment callbacks,
            # including legacy orders without an inbound partner reference.
            key_suffix = (
                status if status in ("accepted", "rejected")
                else "transition:%s:%s" % (status, latest.id or 0)
            )
            log = self.env["blend.integration.log"].enqueue(
                order.config_id,
                event,
                payload,
                order=order,
                idempotency_key="%s:%s:%s:%s"
                % (event, order.config_id.id, order.uuid or order.id, key_suffix),
                external_reference=order.uuid or str(order.id),
            )
            if not latest or log.id > latest.id:
                # Under REPEATABLE READ a row lock alone does not refresh a
                # competing transaction's snapshot. Persist a write on the order
                # whenever a new intent is created, before releasing its lock.
                order.blend_status_sync_revision += 1
                order.flush_recordset(["blend_status_sync_revision"])
            if order.blend_partner_reference and status in ("accepted", "rejected"):
                order.blend_decision_log_id = log
        return True

    def action_blend_send_delivery_status(self):
        # Keep the legacy selector from bypassing the manual decision workflow.
        if any(order.blend_is_inbound and order.blend_delivery_status in ("accepted", "rejected") for order in self):
            self.ensure_one()
            if self.blend_delivery_status == "accepted":
                return self.action_blend_accept_order()
            return self.action_blend_reject_order()
        for order in self:
            if not order.blend_delivery_status:
                raise ValidationError(_("Select a delivery status before sending it to Blend."))
            order._blend_enqueue_status(order.blend_delivery_status)
        return True

    def action_blend_retry(self):
        logs = self.env["blend.integration.log"].search(
            [("order_id", "in", self.ids), ("direction", "=", "outbound"), ("state", "in", ("failed", "retry"))]
        )
        if logs:
            logs.action_retry()
        return True
