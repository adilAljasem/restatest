import json
import logging
import uuid
from collections import defaultdict

import psycopg2
import pytz

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..exceptions import DeliveryIntegrationError, DeliveryUnsupportedOperation
from .delivery_order import _INTERNAL_ORDER_UPDATE_TOKEN


_logger = logging.getLogger(__name__)
KITCHEN_AUTO_PRINT_MAX_AGE_MINUTES = 15


class DeliveryPosSessionUnavailable(UserError):
    """The only acceptance preflight failure that may wait for a POS session."""


class DeliveryOrderWorkflow(models.Model):
    _inherit = "bs.delivery.order"
    _rec_name = "name"

    def _compare_provider_financial_amounts(self, expected, actual):
        """Compare provider and Odoo amounts at the currency's precision."""
        self.ensure_one()
        return self.currency_id.compare_amounts(expected, actual)

    def _tax_rounding_tolerance(self):
        self.ensure_one()
        return max(0.0, self.connection_id.tax_rounding_tolerance or 0.0)

    def _is_tax_rounding_difference(self, provider_tax, odoo_tax):
        """True when only rounding separates the platform's tax from Odoo's.

        Platforms round VAT on the order total, Odoo on each line, so a
        multi-item order can differ by a few halalas with the same total.
        """
        self.ensure_one()
        difference = self.currency_id.round(abs((provider_tax or 0.0) - (odoo_tax or 0.0)))
        return self.currency_id.compare_amounts(difference, self._tax_rounding_tolerance()) <= 0

    def _accept_tax_rounding_difference(self, odoo_tax):
        """Use Odoo's tax for the order and keep the platform's value in chatter."""
        self.ensure_one()
        provider_tax = self.vat_amount
        odoo_tax = self.currency_id.round(odoo_tax)
        self.sudo()._with_delivery_internal_update().write({"vat_amount": odoo_tax})
        self.message_post(body=_(
            "Tax rounding difference accepted: the platform sent %(provider)s, Odoo "
            "computes %(odoo)s for the same total. Odoo's tax is used.",
            provider=provider_tax, odoo=odoo_tax,
        ))

    name = fields.Char(
        string="Order Number",
        required=True,
        default="New",
        index=True,
        copy=False,
        tracking=True,
    )
    pos_config_id = fields.Many2one(
        "pos.config",
        compute="_compute_pos_config_id",
        store=True,
        index=True,
        check_company=True,
    )
    delivery_invoice_partner_id = fields.Many2one(
        "res.partner", string="Selected Invoice Customer", copy=False,
        check_company=True,
    )
    sync_state = fields.Selection(
        [
            ("not_required", "Not Required"),
            ("pending", "Pending"),
            ("synced", "Synced"),
            ("not_supported", "Not Supported by Provider"),
            ("error", "Synchronization Error"),
        ],
        default="not_required",
        required=True,
        index=True,
        copy=False,
        tracking=True,
    )
    last_sync_at = fields.Datetime(copy=False, readonly=True)
    last_sync_error = fields.Text(copy=False, readonly=True)
    driver_tracking_url = fields.Char(copy=False)
    delivery_latitude = fields.Float(digits=(10, 7), copy=False)
    delivery_longitude = fields.Float(digits=(10, 7), copy=False)
    small_order_fee = fields.Monetary(currency_field="currency_id", default=0.0)
    tip_amount = fields.Monetary(currency_field="currency_id", default=0.0)
    line_count = fields.Integer(compute="_compute_line_count", store=True)
    kitchen_print_state = fields.Selection(
        [
            ("not_requested", "Not Requested"),
            ("pending", "Pending"),
            ("attempting", "Printing"),
            ("sent", "Sent to Kitchen"),
            ("error", "Print Error"),
        ],
        default="not_requested",
        required=True,
        readonly=True,
        copy=False,
        index=True,
    )
    kitchen_print_requested_at = fields.Datetime(readonly=True, copy=False)
    kitchen_print_last_attempt_at = fields.Datetime(readonly=True, copy=False)
    kitchen_print_sent_at = fields.Datetime(readonly=True, copy=False)
    kitchen_print_attempts = fields.Integer(readonly=True, copy=False, default=0)
    kitchen_print_error = fields.Text(readonly=True, copy=False)
    kitchen_print_claim_key = fields.Char(readonly=True, copy=False, index=True)
    kitchen_print_claim_until = fields.Datetime(readonly=True, copy=False, index=True)
    kitchen_manual_retry_requested = fields.Boolean(readonly=True, copy=False)
    kitchen_can_retry = fields.Boolean(compute="_compute_kitchen_can_retry")

    @api.depends("pos_order_id", "state", "kitchen_print_state",
                 "kitchen_print_attempts")
    def _compute_kitchen_can_retry(self):
        for order in self:
            order.kitchen_can_retry = bool(
                order.pos_order_id
                and order.state in ("accepted", "preparing", "ready")
                and (order.kitchen_print_state == "error"
                     or (order.kitchen_print_state == "attempting"
                         and order.kitchen_print_attempts)
                     or (order.kitchen_print_state == "pending"
                         and order.kitchen_print_attempts
                         and not order._is_kitchen_manual_retry_requested()))
            )

    _name_company_unique = models.Constraint(
        "UNIQUE(company_id, name)",
        "Delivery order numbers must be unique per company.",
    )

    @api.depends("store_binding_id.pos_config_id", "pos_order_id.session_id.config_id")
    def _compute_pos_config_id(self):
        for order in self:
            order.pos_config_id = (
                order.store_binding_id.pos_config_id
                or order.pos_order_id.session_id.config_id
            )

    @api.depends("line_ids", "line_ids.is_modifier")
    def _compute_line_count(self):
        for order in self:
            order.line_count = len(order.line_ids.filtered(lambda line: not line.is_modifier))

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for incoming in vals_list:
            values = dict(incoming)
            connection = self.env["bs.delivery.connection"].browse(
                values.get("connection_id")
            ).exists()
            store = self.env["bs.delivery.store.binding"].browse(
                values.get("store_binding_id")
            ).exists()
            if store:
                values.setdefault("late_threshold_minutes", store.late_threshold_minutes)
            if values.get("name", "New") == "New":
                company = connection.company_id or self.env.company
                values["name"] = (
                    self.env["ir.sequence"]
                    .with_company(company)
                    .next_by_code("bs.delivery.order")
                    or "New"
                )
            prepared.append(values)
        orders = super().create(prepared)
        for order in orders:
            order._ensure_compatibility_binding()
            order._notify_pos_board("new_order")
            if not (
                self.env.context.get("bs_delivery_defer_initial_acceptance")
                and self.env.context.get("bs_delivery_internal_update") is _INTERNAL_ORDER_UPDATE_TOKEN
            ):
                order._apply_initial_acceptance_policy()
        return orders

    def _ensure_compatibility_binding(self):
        self.ensure_one()
        if self.binding_id:
            return self.binding_id
        binding, _created = self.env["bs.delivery.order.binding"].sudo().get_or_create(
            self.connection_id,
            self.external_order_id,
            {
                "external_reference": self.external_reference,
                "external_source": self.external_source or self.provider_id.code,
                "external_status": self.external_status,
                "odoo_status": self._binding_state(self.state),
                "pos_order_id": self.pos_order_id.id or False,
            },
        )
        self.sudo()._with_delivery_internal_update().write(
            {"binding_id": binding.id}
        )
        return binding

    @staticmethod
    def _binding_state(state):
        return {
            "received": "pending",
            "accepted": "accepted",
            "preparing": "preparing",
            "ready": "ready",
            "picked_up": "dispatched",
            "delivered": "completed",
            "rejected": "rejected",
            "cancelled": "cancelled",
        }.get(state, "pending")

    def _apply_initial_acceptance_policy(self):
        for order in self.filtered(lambda value: value.state == "received"):
            store = order.store_binding_id
            if store and store.is_paused:
                order.transition_to(
                    "rejected", source="automatic", expected_state="received",
                    reject_reason_id=store.pause_reject_reason_id,
                    reason=store.pause_reason or _("The remote store is temporarily paused."),
                    idempotency_key="paused-store:%s" % order.id,
                )
                continue
            if store and store.work_time_enabled and not store.is_in_delivery_work_time():
                order.transition_to(
                    "rejected", source="automatic", expected_state="received",
                    reject_reason_id=order.connection_id.delivery_auto_reject_reason_id,
                    reason=_("Rejected: Store is currently outside operational work hours."),
                    idempotency_key="outside-work-time:%s" % order.id,
                )
                continue
            connection = order.connection_id
            action = connection.delivery_accept_action if connection else "manual"
            if action == "reject":
                order.transition_to(
                    "rejected", source="automatic", expected_state="received",
                    reject_reason_id=connection.delivery_auto_reject_reason_id,
                    reason=_("Rejected by the POS branch delivery policy."),
                    idempotency_key="branch-reject:%s" % order.id,
                )
            elif action == "accept":
                order.transition_to(
                    "accepted", source="automatic", expected_state="received",
                    idempotency_key="branch-accept:%s" % order.id,
                )

    def _after_delivery_state_transition(self, transition):
        result = super()._after_delivery_state_transition(transition)
        self.ensure_one()
        if (transition.to_state == "accepted" and not self.pos_order_id
                and self.pos_processing_state != "waiting_session"):
            try:
                # Keep an unavailable/misconfigured POS from leaving a partial
                # draft order behind when acceptance itself is allowed to commit.
                with self.env.cr.savepoint():
                    self.create_pos_order()
            except UserError as error:
                self._set_pos_processing_result("blocked", str(error))
                self.message_post(
                    body=_("POS order creation is waiting: %s") % str(error),
                    message_type="comment",
                )
        if transition.to_state == "accepted":
            self._request_kitchen_autoprint()

        binding = self._ensure_compatibility_binding()
        binding.sudo().write(
            {
                "odoo_status": self._binding_state(transition.to_state),
                "external_status": self.external_status,
                "pos_order_id": self.pos_order_id.id or binding.pos_order_id.id or False,
            }
        )
        if transition.is_noop or transition.source in ("provider", "webhook"):
            self.sudo()._with_delivery_internal_update().write(
                {
                    "sync_state": "not_required",
                    "last_sync_error": False,
                }
            )
            self._notify_pos_board("state_changed")
            return result

        operation_map = {
            "accepted": "accept_order",
            "preparing": "mark_order_preparing",
            "ready": "mark_order_ready",
            "picked_up": "mark_order_picked_up",
            "delivered": "mark_order_delivered",
            "rejected": "reject_order",
            "cancelled": "cancel_order",
        }
        operation = operation_map.get(transition.to_state)
        try:
            adapter = self.connection_id._get_adapter()
        except DeliveryUnsupportedOperation as error:
            self.sudo()._with_delivery_internal_update().write(
                {
                    "sync_state": "not_supported",
                    "last_sync_error": str(error)[:4000],
                }
            )
            self._notify_pos_board("state_changed")
            return result
        except DeliveryIntegrationError as error:
            self.sudo()._with_delivery_internal_update().write(
                {"sync_state": "error", "last_sync_error": str(error)[:4000]}
            )
            self._notify_pos_board("state_changed")
            return result
        except psycopg2.Error:
            # A database error has already aborted the transaction: recording
            # sync_state would fail with InFailedSqlTransaction. Re-raise so
            # Odoo rolls back and replays a deadlock/serialization failure.
            raise
        except Exception as error:
            _logger.exception(
                "Unable to resolve the adapter for delivery transition %s",
                transition.id,
            )
            self.sudo()._with_delivery_internal_update().write(
                {"sync_state": "error", "last_sync_error": str(error)[:4000]}
            )
            self._notify_pos_board("state_changed")
            return result
        if operation and not adapter.supports(operation) and adapter.supports("update_order"):
            operation = "update_order"
        if not operation or not adapter.supports(operation) or not adapter.supports_order_state(transition.to_state):
            self.sudo()._with_delivery_internal_update().write(
                {"sync_state": "not_supported", "last_sync_error": False}
            )
            self._notify_pos_board("state_changed")
            return result

        idempotency_key = "delivery-transition:%s:%s" % (self.id, transition.id)
        reason = transition.reason
        if transition.to_state == "rejected" and transition.reject_reason_id:
            # Provider adapters expect their stable rejection code, while the
            # free-form operator note remains preserved on the transition audit.
            reason = (
                transition.reject_reason_id.external_code
                or transition.reject_reason_id.code
                or transition.reason
            )
        status_value = transition.to_state if operation == "update_order" else None
        try:
            job, _created = self.env["bs.delivery.outbox"].sudo().enqueue(
                self.connection_id,
                operation,
                binding,
                status_value=status_value,
                reason=reason,
                idempotency_key=idempotency_key,
            )
        except psycopg2.Error:
            # A database error has already aborted the transaction: recording
            # sync_state would fail with InFailedSqlTransaction. Re-raise so
            # Odoo rolls back and replays a deadlock/serialization failure.
            raise
        except Exception as error:
            _logger.exception("Unable to queue delivery transition %s", transition.id)
            self.sudo()._with_delivery_internal_update().write(
                {"sync_state": "error", "last_sync_error": str(error)[:4000]}
            )
        else:
            self.sudo()._with_delivery_internal_update().write(
                {
                    "sync_state": "pending" if job.state != "done" else "synced",
                    "last_sync_at": job.processed_at,
                    "last_sync_error": False,
                }
            )
        self._notify_pos_board("state_changed")
        return result

    def _manual_transition(self, target_state, *, reason=None, reject_reason_id=None):
        if not self.env.user.has_group(
            "bs_pos_delivery_core.group_delivery_operator"
        ):
            raise AccessError(_("Delivery operator access is required."))
        self.check_access("write")
        orders = self.sudo().browse(self.ids)._with_delivery_actor(self.env.user)
        if orders.filtered(lambda order: order.company_id not in self.env.companies):
            raise AccessError(_("The delivery order belongs to an unavailable company."))
        for order in orders:
            order.transition_to(
                target_state,
                source="manual",
                expected_state=order.state,
                reason=reason,
                reject_reason_id=reject_reason_id,
                idempotency_key="backend:%s:%s:%s"
                % (order.id, target_state, uuid.uuid4()),
            )
        return True

    def action_accept(self):
        self.ensure_one()
        if self._needs_manual_invoice_customer():
            if not self.env.user.has_group("bs_pos_delivery_core.group_delivery_operator"):
                raise AccessError(_("Delivery operator access is required."))
            self.check_access("write")
            if self.company_id not in self.env.companies:
                raise AccessError(_("The delivery order belongs to an unavailable company."))
            action = self.env.ref(
                "bs_pos_delivery_core.action_bs_delivery_accept_customer_wizard"
            ).read()[0]
            action["context"] = {
                "default_order_id": self.id,
                "default_company_id": self.company_id.id,
            }
            return action
        return self._manual_transition("accepted")

    def _needs_manual_invoice_customer(self):
        self.ensure_one()
        connection = self.connection_id
        return not (
            self.delivery_invoice_partner_id
            or any((self.customer_name, self.customer_phone,
                    self.customer_email, self.delivery_address))
            or (connection.delivery_accept_action == "accept"
                and connection.delivery_default_invoice_partner_id)
        )

    def _set_delivery_invoice_partner(self, partner_id):
        self.ensure_one()
        try:
            partner_key = int(partner_id or 0)
        except (TypeError, ValueError):
            partner_key = 0
        partner = self.env["res.partner"].browse(partner_key).exists()
        if not partner or not partner.active:
            raise UserError(_("Choose an active invoice customer."))
        if partner.company_id and partner.company_id != self.company_id:
            raise AccessError(_("The invoice customer belongs to another company."))
        self.sudo()._with_delivery_internal_update().write({
            "delivery_invoice_partner_id": partner.id,
        })

    def action_start_preparing(self):
        return self._manual_transition("preparing")

    def action_mark_ready(self):
        return self._manual_transition("ready")

    def action_mark_picked_up(self):
        return self._manual_transition("picked_up")

    def action_mark_delivered(self):
        return self._manual_transition("delivered")

    def action_cancel(self):
        return self._manual_transition("cancelled", reason=_("Cancelled in Odoo"))

    def action_open_reject_wizard(self):
        self.ensure_one()
        action = self.env.ref("bs_pos_delivery_core.action_bs_delivery_reject_wizard").read()[0]
        action["context"] = {
            "default_order_id": self.id,
            "default_company_id": self.company_id.id,
            "default_provider_id": self.provider_id.id,
        }
        return action

    def _create_pos_order_hook(self):
        self.ensure_one()
        store = self.store_binding_id
        if not store:
            raise UserError(_("Map the remote store to a Point of Sale first."))
        if self.currency_id != store.pos_config_id.currency_id:
            raise UserError(
                _(
                    "The platform order currency (%(order)s) must match the target POS "
                    "currency (%(pos)s).",
                    order=self.currency_id.name,
                    pos=store.pos_config_id.currency_id.name,
                )
            )
        unmapped = self.line_ids.filtered(
            lambda line: not line.product_id
            and (not line.is_modifier or bool(line.price_subtotal or line.vat_amount))
        )
        if unmapped:
            raise UserError(
                _("Map every platform item before creating the POS order: %s")
                % ", ".join(unmapped.mapped("external_sku") or unmapped.mapped("name"))
            )

        payment_method = self.env["pos.payment.method"]
        if self.contract_version != 1 and self.amount_total > 0 and self.remote_payment_method:
            payment_method = self.env["bs.delivery.payment.service"]._resolve_payment_method(
                self.connection_id, store,
                external_code=self.remote_payment_method or "",
                external_source=self.external_source or "",
                collected_by="unknown",
            )

        line_commands = []
        computed_total = 0.0
        line_mapping = {}
        for line in self._pos_order_item_lines():
            taxes = self._pos_order_line_taxes(line)
            line_uuid = str(uuid.uuid4())
            line_commands.append(
                Command.create(
                    {
                        "uuid": line_uuid,
                        "product_id": line.product_id.id,
                        "name": line.name,
                        "full_product_name": line.name,
                        "qty": line.quantity,
                        "price_unit": self._pos_order_line_price(line, taxes),
                        "discount": (
                            min(
                                100.0,
                                line.discount_amount
                                / (line.price_unit * line.quantity)
                                * 100.0,
                            )
                            if line.price_unit and line.quantity
                            else 0.0
                        ),
                        "price_subtotal": line.price_subtotal,
                        "price_subtotal_incl": line.price_subtotal + line.vat_amount,
                        "tax_ids": [Command.set(taxes.ids)],
                        "customer_note": self._line_note_with_modifiers(line),
                    }
                )
            )
            line_mapping[line.id] = line_uuid
            computed_total += line.price_subtotal + line.vat_amount

        self._append_operational_amount_lines(
            line_commands, computed_total, store,
        )
        line_commands = self.env["bs.delivery.money.engine"]._reconcile_pos_lines(
            self, line_commands,
        )

        session = self.env["pos.session"].search(
            [
                ("config_id", "=", store.pos_config_id.id),
                ("state", "in", ["opening_control", "opened"]),
            ],
            order="id desc",
            limit=1,
        )
        if not session:
            self._check_sessionless_acceptance_preflight(store)
            raise DeliveryPosSessionUnavailable(
                _("No open POS session exists for %s.") % store.pos_config_id.display_name
            )

        pos_values = {
            "session_id": session.id,
            "company_id": self.company_id.id,
            "partner_id": self._resolve_or_create_customer_partner().id or False,
            "pricelist_id": self._pos_order_pricelist(store).id,
            "fiscal_position_id": store.fiscal_position_id.id or False,
            "general_customer_note": self.customer_note or False,
            "lines": line_commands,
            "amount_total": self.amount_total,
            "amount_tax": self.vat_amount,
            "amount_paid": 0.0,
            "amount_return": 0.0,
        }
        preset = self._pos_order_preset(store)
        if preset:
            pos_values["preset_id"] = preset.id
        pos_order = self.env["pos.order"].with_company(self.company_id).with_context(
            bs_delivery_core_import=True
        ).create(pos_values)
        pos_lines_by_uuid = {pl.uuid: pl for pl in pos_order.lines if pl.uuid}
        delivery_pos_line_map = {
            del_id: pos_lines_by_uuid.get(u_id)
            for del_id, u_id in line_mapping.items()
            if u_id in pos_lines_by_uuid
        }
        self._link_pos_order_modifiers_and_combos(pos_order, delivery_pos_line_map)

        self.sudo()._with_delivery_internal_update().write(
            {"pos_order_id": pos_order.id}
        )
        binding = self.binding_id or self._ensure_compatibility_binding()
        binding.sudo().write({"pos_order_id": pos_order.id})
        if self.contract_version == 1:
            payment_service = self.env["bs.delivery.payment.service"].sudo().with_company(self.company_id)
            for allocation in self.payment_allocation_ids.filtered(
                lambda row: row.collection_status == "collected"
                or (row.collection_status == "expected" and row.external_payment_code.strip().casefold() in ("cash", "cod", "1"))
            ):
                payment_service._record_payment(
                    self,
                    {
                        "external_payment_code": allocation.external_payment_code,
                        "external_source": allocation.external_source,
                        "amount": allocation.amount,
                        "currency": self.currency_id.name,
                        "collected_by": allocation.collected_by,
                        "collection_status": allocation.collection_status,
                        **({"external_payment_reference": allocation.external_payment_reference} if allocation.external_payment_reference else {}),
                        **({"external_name": allocation.external_name} if allocation.external_name else {}),
                    },
                    event=allocation.webhook_event_id or self.webhook_event_id,
                    post_expected_cash=allocation.collection_status == "expected",
                    allocation=allocation,
                )
        elif self.amount_total > 0 and payment_method:
            pos_order.add_payment(
                {
                    "pos_order_id": pos_order.id,
                    "payment_method_id": payment_method.id,
                    "amount": self.amount_total,
                    "payment_date": fields.Datetime.now(),
                }
            )
        # Imported orders do not pass through the POS frontend action that
        # normally publishes a restaurant preparation ticket.  Let Odoo's
        # preparation service process the completed Core order once its
        # modifier and combo relations are in place.
        if hasattr(pos_order, "_resta_kds_dispatch"):
            # Resta kitchen screen (resta_kds): what is on the order goes to the kitchen now
            pos_order.sudo().with_context(resta_kds_fire=True)._resta_kds_dispatch(paid_now=True)
        elif "pos.prep.order" in self.env:
            self.env["pos.prep.order"].sudo().process_order(pos_order.id, {})
        return pos_order

    def _pos_order_preset(self, store):
        """Select the branch POS preset for this order's fulfillment type.

        Provider addons may still override this hook (e.g. Blend's own preset
        fields). Without a preset the POS order is invisible under the POS
        Delivery/Takeout tabs and carries no service type to the kitchen.
        """
        self.ensure_one()
        preset = store._delivery_preset_for(self.fulfillment_type) if store else False
        if preset and store._is_usable_delivery_preset(preset):
            return preset
        if preset:
            _logger.warning(
                "Ignoring POS preset %s on store mapping %s: it is no longer "
                "available on POS %s.", preset.id, store.id, store.pos_config_id.id,
            )
        if store and self.fulfillment_type == "delivery":
            return store._auto_delivery_preset()
        return self.env["pos.preset"]

    def _pos_order_pricelist(self, store):
        """Allow provider addons to use the pricelist that published their prices."""
        self.ensure_one()
        return store.pricelist_id or store.pos_config_id.pricelist_id

    def _check_sessionless_acceptance_preflight(self, store):
        """Validate configuration without creating POS, payments or an invoice."""
        self.ensure_one()
        journal = store.pos_config_id.invoice_journal_id
        if not journal or journal.type != "sale" or journal.company_id != self.company_id:
            raise UserError(_("Configure a sales invoice journal on the POS branch."))
        if not (self.delivery_invoice_partner_id or self.connection_id.delivery_default_invoice_partner_id
                or any((self.customer_name, self.customer_phone,
                        self.customer_email, self.delivery_address))):
            raise UserError(_("Choose an invoice customer before accepting the delivery order."))
        if self.contract_version == 1:
            allocations = self.payment_allocation_ids.filtered(
                lambda row: row.collection_status == "collected"
                or (row.collection_status == "expected" and row.external_payment_code.strip().casefold()
                    in ("cash", "cod", "1"))
            )
            if self.currency_id.compare_amounts(sum(allocations.mapped("amount")), self.amount_total):
                raise UserError(_("The delivery POS order must be fully paid before acceptance."))
            payment_service = self.env["bs.delivery.payment.service"]
            for allocation in allocations:
                payment_service._resolve_payment_method(
                    self.connection_id, store,
                    external_code=allocation.external_payment_code,
                    external_source=allocation.external_source,
                    collected_by=allocation.collected_by,
                )
        elif self.amount_total > 0 and not self.remote_payment_method:
            raise UserError(_("The delivery POS order must be fully paid before acceptance."))

    def _pos_order_item_lines(self):
        """Provider hook for modifiers represented by separately priced POS lines."""
        self.ensure_one()
        if self.contract_version == 1:
            return self.line_ids.filtered(lambda line: not line.is_modifier or line.product_id)
        return self.line_ids.filtered(lambda item: not item.is_modifier)

    def _link_pos_order_modifiers_and_combos(self, pos_order, delivery_pos_line_map):
        pos_line_model = self.env["pos.order.line"]
        has_pos_modifiers = "is_modifier_line" in pos_line_model._fields
        has_pos_combos = "combo_parent_id" in pos_line_model._fields
        if not (has_pos_modifiers or has_pos_combos):
            return

        modifier_option_model = (
            self.env["pos.modifier.option"]
            if "pos.modifier.option" in self.env
            else None
        )
        combo_item_model = (
            self.env["product.combo.item"]
            if "product.combo.item" in self.env
            else None
        )

        parent_selection_data = defaultdict(dict)
        for d_line in self.line_ids.filtered(lambda line: line.is_modifier and line.parent_line_id):
            child_pos_line = delivery_pos_line_map.get(d_line.id)
            parent_pos_line = delivery_pos_line_map.get(d_line.parent_line_id.id)
            if not child_pos_line or not parent_pos_line:
                continue

            linked = False
            # 1. Try matching Modifier Option
            if has_pos_modifiers and modifier_option_model is not None:
                parent_template = parent_pos_line.product_id.product_tmpl_id
                if getattr(parent_template, "allow_modifiers", False) and hasattr(parent_template, "modifier_group_line_ids"):
                    group_ids = parent_template.modifier_group_line_ids.modifier_group_id.ids
                    ext_id = str(d_line.external_product_id or "").strip()
                    option = modifier_option_model.browse()
                    if ext_id.startswith("posmod-") and ext_id[7:].isdecimal():
                        candidate = modifier_option_model.sudo().browse(int(ext_id[7:])).exists()
                        candidate_line = parent_template.modifier_group_line_ids.filtered(
                            lambda row: row.modifier_group_id == candidate.group_id
                        )[:1] if candidate else False
                        effective = candidate_line._effective_option(candidate) if candidate_line else candidate
                        if (candidate and candidate.active and candidate.group_id.id in group_ids
                                and effective.product_id == child_pos_line.product_id
                                and candidate.option_type == "product"):
                            option = candidate
                    if not option:
                        option = modifier_option_model.sudo().search([
                            ("group_id", "in", group_ids),
                            ("product_id", "=", child_pos_line.product_id.id),
                            ("active", "=", True),
                        ], limit=1)
                    if not option and d_line.external_product_id:
                        if ext_id.isdecimal():
                            cand = modifier_option_model.sudo().browse(int(ext_id)).exists()
                            if cand and cand.group_id.id in group_ids:
                                option = cand
                        if not option:
                            option = modifier_option_model.sudo().search([
                                ("blend_external_id", "=", ext_id),
                                ("group_id", "in", group_ids),
                                ("active", "=", True),
                            ], limit=1)
                    if option:
                        qty_per_parent = (
                            child_pos_line.qty / parent_pos_line.qty
                            if parent_pos_line.qty
                            else child_pos_line.qty
                        )
                        is_included = (child_pos_line.price_unit == 0.0 or option.price_mode == "included")
                        child_pos_line.write({
                            "is_modifier_line": True,
                            "modifier_parent_line_id": parent_pos_line.id,
                            "modifier_option_id": option.id,
                            "modifier_option_name": option.name,
                            "modifier_qty_per_parent": qty_per_parent,
                            "modifier_is_included": is_included,
                            "modifier_configuration_key": f"{option.id}:{qty_per_parent}:{child_pos_line.price_unit}",
                        })
                        parent_selection_data[parent_pos_line][str(option.id)] = (
                            parent_selection_data[parent_pos_line].get(str(option.id), 0.0)
                            + qty_per_parent
                        )
                        linked = True

            # 2. Try matching Combo Item
            if not linked and has_pos_combos and combo_item_model is not None:
                parent_template = parent_pos_line.product_id.product_tmpl_id
                if hasattr(parent_template, "combo_ids") and parent_template.combo_ids:
                    combo_ids = parent_template.combo_ids.ids
                    ext_id = str(d_line.external_product_id or "").strip()
                    combo_item = combo_item_model.browse()
                    if ext_id.startswith("comboitem-") and ext_id[10:].isdecimal():
                        candidate = combo_item_model.sudo().browse(int(ext_id[10:])).exists()
                        if (candidate and candidate.combo_id.id in combo_ids
                                and candidate.product_id == child_pos_line.product_id):
                            combo_item = candidate
                    if not combo_item:
                        combo_item = combo_item_model.sudo().search([
                            ("combo_id", "in", combo_ids),
                            ("product_id", "=", child_pos_line.product_id.id),
                        ], limit=1)
                    if not combo_item and d_line.external_product_id:
                        if ext_id.isdecimal():
                            cand = combo_item_model.sudo().browse(int(ext_id)).exists()
                            if cand and cand.combo_id.id in combo_ids:
                                combo_item = cand
                    if combo_item:
                        combo_vals = {
                            "combo_parent_id": parent_pos_line.id,
                            "combo_item_id": combo_item.id,
                        }
                        if "combo_item_extra_price" in child_pos_line._fields:
                            combo_vals["combo_item_extra_price"] = getattr(combo_item, "extra_price", 0.0) or 0.0
                        child_pos_line.write(combo_vals)
                        linked = True

        for parent_line, sel_data in parent_selection_data.items():
            if "modifier_selection_data" in parent_line._fields:
                parent_line.write({
                    "modifier_selection_data": sel_data,
                    "modifier_configuration_key": repr(sorted(sel_data.items())),
                })

    def _pos_order_line_taxes(self, line):
        return line.product_id.taxes_id.filtered(
            lambda tax: not tax.company_id or tax.company_id == self.company_id
        )

    def _pos_order_line_price(self, line, taxes):
        if self.contract_version == 1 and taxes:
            effective_taxes = self.store_binding_id.fiscal_position_id.map_tax(taxes)
            details = effective_taxes._get_tax_details(
                line.price_unit,
                line.quantity,
                precision_rounding=self.currency_id.rounding,
                rounding_method="round_globally",
                product=line.product_id,
                special_mode="total_excluded",
            )
            included = sum(
                tax["tax_amount"] for tax in details["taxes_data"]
                if tax["tax"].price_include
            )
            return line.price_unit + included / line.quantity
        return line.price_unit

    def _resolve_or_create_customer_partner(self):
        """Resolve a delivery customer without ever matching on a name alone."""
        self.ensure_one()
        name = str(self.customer_name or "").strip()
        phone = str(self.customer_phone or "").strip()
        email = str(self.customer_email or "").strip().lower()
        address = str(self.delivery_address or "").strip()
        if not any((name, phone, email, address)):
            if self.delivery_invoice_partner_id:
                return self.delivery_invoice_partner_id
            connection = self.connection_id
            if connection.delivery_accept_action == "accept":
                return connection.delivery_default_invoice_partner_id
            return self.env["res.partner"]

        Partner = self.env["res.partner"].sudo().with_company(self.company_id)
        company_scope = [
            "|",
            ("company_id", "=", False),
            ("company_id", "=", self.company_id.id),
        ]
        partner = self.env["res.partner"]
        if email:
            partner = Partner.search(
                company_scope + [("email", "=ilike", email)],
                order="company_id desc, id",
                limit=1,
            )
        if not partner and phone:
            phone_domain = [("phone", "=", phone)]
            if "mobile" in Partner._fields:
                phone_domain = [
                    "|",
                    ("phone", "=", phone),
                    ("mobile", "=", phone),
                ]
            partner = Partner.search(
                company_scope + phone_domain,
                order="company_id desc, id",
                limit=1,
            )
        if partner:
            return partner

        values = {
            "name": name or phone or email or _("Delivery Customer"),
            "phone": phone or False,
            "email": email or False,
            "street": address or False,
            "company_id": self.company_id.id,
        }
        if "customer_rank" in Partner._fields:
            values["customer_rank"] = 1
        return Partner.with_context(
            tracking_disable=True,
            mail_create_nosubscribe=True,
        ).create(values)

    def _append_operational_amount_lines(self, commands, current_total, store):
        values = (
            (self.delivery_fee, store.delivery_fee_product_id, _("Delivery fee")),
            (self.service_fee + self.small_order_fee, store.service_fee_product_id, _("Service fee")),
            (self.tip_amount, store.tip_product_id, _("Tip")),
        )
        for amount, product, label in values:
            if not self.currency_id.is_zero(amount):
                if not product:
                    raise UserError(_("Configure a product for %s.") % label)
                commands.append(
                    self.env["bs.delivery.money.engine"]._prepare_fee_line(
                        self, product, amount, label,
                    ),
                )
                current_total += amount
        if self.untaxed_discounts:
            product = self._untaxed_discount_product()
            taxes = self._untaxed_discount_line_taxes()
            for component in self.untaxed_discounts:
                commands.append(self._pos_adjustment_command(
                    product, -component["amount"], component["label"], taxes,
                ))
                current_total -= component["amount"]
        return current_total

    def _untaxed_discount_line_taxes(self):
        """Provider hook: default discounts retain the existing tax-free POS line."""
        self.ensure_one()
        return self.env["account.tax"]

    def _untaxed_discount_product(self):
        """Keep order-level discounts off item tax bases and on POS invoices."""
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", (194019, self.company_id.id),
        )
        Product = self.env["product.product"].sudo().with_company(self.company_id)
        code = "BS-DELIVERY-UNTAXED-DISCOUNT-%s" % self.company_id.id
        product = Product.search([
            ("default_code", "=", code), ("company_id", "=", self.company_id.id),
        ], limit=1)
        if not product:
            product = Product.create({
                "name": _("Delivery Order Discount"),
                "default_code": code,
                "company_id": self.company_id.id,
                "type": "service",
                "sale_ok": False,
                "purchase_ok": False,
                "available_in_pos": False,
                "taxes_id": [Command.clear()],
                "list_price": 0.0,
                "responsible_id": False,
            })
        if (product.taxes_id or product.type != "service"
                or product.sale_ok or product.available_in_pos):
            raise UserError(_("Delivery order discount product must be an untaxed hidden service."))
        return product

    @staticmethod
    def _pos_adjustment_command(product, amount, label, taxes):
        return Command.create(
            {
                "product_id": product.id,
                "name": label,
                "full_product_name": label,
                "qty": 1.0,
                "price_unit": amount,
                "price_subtotal": amount,
                "price_subtotal_incl": amount,
                "tax_ids": [Command.set(taxes.ids)] if taxes else [Command.clear()],
            }
        )

    @staticmethod
    def _line_note_with_modifiers(line):
        notes = [line.customer_note] if line.customer_note else []
        if line.modifier_text:
            notes.append(line.modifier_text)
        modifiers = line.modifier_line_ids.mapped("name")
        if modifiers:
            notes.append(line.env._("Options: %s") % ", ".join(modifiers))
        return "\n".join(notes) or False

    @api.model
    def upsert_from_binding(self, binding, webhook_event=None):
        """Mirror legacy POS orders into the canonical order model."""
        binding.ensure_one()
        pos_order = binding.pos_order_id
        if not pos_order:
            return self.browse()
        connection = binding.connection_id
        store = self._ensure_store_binding(connection, pos_order.session_id.config_id)
        desired_state = {
            "pending": "received",
            "accepted": "accepted",
            "preparing": "preparing",
            "ready": "ready",
            "dispatched": "picked_up",
            "completed": "delivered",
            "cancelled": "cancelled",
            "rejected": "rejected",
        }.get(binding.odoo_status, "received")
        financial_values = self._legacy_financial_values(pos_order, connection, store)
        values = {
            "connection_id": connection.id,
            "store_binding_id": store.id,
            "binding_id": binding.id,
            "webhook_event_id": webhook_event.id if webhook_event else False,
            "pos_order_id": pos_order.id,
            "external_order_id": binding.external_order_id,
            "external_reference": binding.external_reference,
            "external_source": binding.external_source or connection.provider_id.code,
            "external_status": binding.external_status,
            "state": desired_state,
            "currency_id": pos_order.currency_id.id,
            **financial_values,
            "customer_name": pos_order.partner_id.name or False,
            "customer_phone": (
                pos_order.partner_id.phone
                or getattr(pos_order.partner_id, "mobile", False)
                or False
            ),
            "customer_email": pos_order.partner_id.email or False,
            "delivery_address": pos_order.partner_id.contact_address or False,
            "customer_note": getattr(pos_order, "general_customer_note", False),
            **self._legacy_driver_values(pos_order),
            "scheduled_at": self._legacy_scheduled_at(pos_order),
            "received_at": pos_order.date_order or pos_order.create_date,
            "is_prepaid": bool(pos_order.amount_total > 0 and pos_order.amount_paid >= pos_order.amount_total),
            "remote_payment_method": self._legacy_payment_method(pos_order),
            "fulfillment_type": self._legacy_fulfillment_type(pos_order),
            "line_ids": self._legacy_line_commands(
                pos_order,
                connection=connection,
                store=store,
            ),
        }
        order = self.sudo().search([("binding_id", "=", binding.id)], limit=1)
        if not order:
            return self.sudo()._with_delivery_internal_update().create(values)
        values.pop("state", None)
        line_commands = values.pop("line_ids", [])
        if order.state == "delivered":
            for field_name in order._delivery_financial_field_names():
                values.pop(field_name, None)
        else:
            values["line_ids"] = [Command.clear(), *line_commands]
        order.sudo()._with_delivery_internal_update().write(values)
        order._sync_legacy_binding_state(
            desired_state,
            binding,
            webhook_event=webhook_event,
        )
        return order

    def _sync_legacy_binding_state(self, desired_state, binding, webhook_event=None):
        """Advance a canonical order from an authoritative legacy/provider state.

        Provider notifications sometimes skip intermediate operational states.  We
        replay only the legal forward path so every committed step remains audited;
        regressions and illegal terminal jumps are ignored rather than bypassing the
        transition service.
        """
        self.ensure_one()
        binding.ensure_one()
        if desired_state == self.state:
            return self.env["bs.delivery.order.transition"]

        source = "webhook" if webhook_event else "provider"
        source_token = (
            "event:%s" % webhook_event.id
            if webhook_event
            else "binding:%s:%s"
            % (binding.id, binding.external_status or desired_state)
        )
        progressive_states = [
            "received",
            "accepted",
            "preparing",
            "ready",
            "picked_up",
            "delivered",
        ]
        transitions = self.env["bs.delivery.order.transition"]

        if desired_state in ("rejected", "cancelled"):
            if desired_state not in self._ALLOWED_TRANSITIONS.get(self.state, ()):
                _logger.warning(
                    "Ignoring illegal provider transition for delivery order %s: %s -> %s",
                    self.id,
                    self.state,
                    desired_state,
                )
                return transitions
            return self.transition_to(
                desired_state,
                source=source,
                expected_state=self.state,
                reason=_("Status received from %(provider)s", provider=self.provider_id.display_name),
                idempotency_key="%s:%s" % (source_token, desired_state),
                external_status=binding.external_status,
            )

        if self.state not in progressive_states or desired_state not in progressive_states:
            return transitions
        current_index = progressive_states.index(self.state)
        desired_index = progressive_states.index(desired_state)
        if desired_index <= current_index:
            _logger.info(
                "Ignoring stale provider state for delivery order %s: %s -> %s",
                self.id,
                self.state,
                desired_state,
            )
            return transitions

        for next_state in progressive_states[current_index + 1 : desired_index + 1]:
            current_state = self.state
            if next_state not in self._ALLOWED_TRANSITIONS.get(current_state, ()):
                _logger.warning(
                    "Stopped illegal provider state jump for delivery order %s: %s -> %s",
                    self.id,
                    current_state,
                    next_state,
                )
                break
            transitions |= self.transition_to(
                next_state,
                source=source,
                expected_state=current_state,
                idempotency_key="%s:%s" % (source_token, next_state),
                external_status=binding.external_status,
            )
        return transitions

    @api.model
    def _ensure_store_binding(self, connection, pos_config):
        store = self.env["bs.delivery.store.binding"].sudo().search(
            [("connection_id", "=", connection.id), ("pos_config_id", "=", pos_config.id)],
            limit=1,
        )
        if store:
            return store
        remote_store_id = (
            str(pos_config.id)
            if connection.provider_id.code == "feedus"
            else "odoo-pos-%s" % pos_config.id
        )
        return self.env["bs.delivery.store.binding"].sudo().create(
            {
                "connection_id": connection.id,
                "pos_config_id": pos_config.id,
                "remote_store_id": remote_store_id,
            }
        )

    @api.model
    def _legacy_line_commands(self, pos_order, connection=None, store=None):
        connection = connection or self.env["bs.delivery.connection"]
        operational_ids = self._legacy_operational_products(store)
        eligible = pos_order.lines.filtered(
            lambda line: (
                line.product_id.id not in operational_ids
                and line.qty > 0
                and line.price_unit >= 0
                and line.price_subtotal >= 0
            )
        )
        parent_keys = {
            str(line.id): line
            for line in eligible
            if not self._legacy_parent_reference(line)
        }
        parent_keys.update(
            {
                str(line.uuid): line
                for line in parent_keys.values()
                if getattr(line, "uuid", False)
            }
        )
        children = defaultdict(list)
        roots = []
        for line in eligible:
            reference = self._legacy_parent_reference(line)
            parent = parent_keys.get(reference) if reference else False
            if parent:
                children[parent.id].append(line)
            else:
                roots.append(line)

        commands = []
        for index, line in enumerate(roots, start=1):
            values = self._legacy_line_values(
                line,
                connection=connection,
                store=store,
                sequence=index * 10,
            )
            modifier_commands = [
                Command.create(
                    self._legacy_line_values(
                        modifier,
                        connection=connection,
                        store=store,
                        sequence=modifier_index * 10,
                        is_modifier=True,
                    )
                )
                for modifier_index, modifier in enumerate(
                    children.get(line.id, []), start=1
                )
            ]
            if modifier_commands:
                values["modifier_line_ids"] = modifier_commands
            commands.append(Command.create(values))
        return commands

    @api.model
    def _legacy_line_values(
        self,
        line,
        *,
        connection,
        store,
        sequence,
        is_modifier=False,
    ):
        mapping = self.env["bs.delivery.product.binding"]
        if connection:
            mapping = mapping.resolve_binding_for_product(
                connection,
                line.product_id,
                store_binding=store,
            )
        gross = max(0.0, line.price_unit * line.qty)
        subtotal = max(0.0, line.price_subtotal)
        external_id = (
            mapping.external_product_id
            if mapping
            else line.product_id.default_code or str(line.product_id.id)
        )
        return {
            "sequence": sequence,
            "external_line_id": str(getattr(line, "uuid", False) or line.id),
            "external_product_id": external_id,
            "external_variant_id": mapping.external_variant_id if mapping else False,
            "external_sku": external_id,
            "name": (
                mapping.external_name_en
                or mapping.external_name
                or line.full_product_name
                or line.name
            ),
            "name_ar": mapping.external_name_ar if mapping else False,
            "product_id": line.product_id.id,
            "quantity": line.qty,
            "price_unit": max(0.0, line.price_unit),
            "discount_amount": max(0.0, gross - subtotal),
            "vat_amount": max(0.0, line.price_subtotal_incl - line.price_subtotal),
            "price_subtotal": subtotal,
            "customer_note": line.customer_note or getattr(line, "note", False),
            "is_modifier": is_modifier,
        }

    @staticmethod
    def _legacy_parent_reference(line):
        if "modifier_parent_uuid" in line._fields and line.modifier_parent_uuid:
            return str(line.modifier_parent_uuid)
        if "modifier_parent_line_id" in line._fields and line.modifier_parent_line_id:
            return str(line.modifier_parent_line_id.id)
        if "combo_parent_id" in line._fields and line.combo_parent_id:
            return str(line.combo_parent_id.id)
        if "parent_line_id" in line._fields and line.parent_line_id:
            return str(line.parent_line_id)
        return False

    @api.model
    def _legacy_financial_values(self, pos_order, connection, store):
        currency = pos_order.currency_id
        operational_ids = self._legacy_operational_products(store)
        totals = {"delivery_fee": 0.0, "service_fee": 0.0, "tip_amount": 0.0}
        items_subtotal = 0.0
        explicit_discount = 0.0
        for line in pos_order.lines:
            kind = operational_ids.get(line.product_id.id)
            if kind:
                totals[kind] += max(0.0, line.price_subtotal)
                continue
            if line.qty > 0 and line.price_unit >= 0:
                gross = max(0.0, line.price_unit * line.qty)
                items_subtotal += gross
                explicit_discount += max(0.0, gross - line.price_subtotal)
            elif line.price_subtotal < 0:
                explicit_discount += abs(line.price_subtotal)

        amount_total = max(0.0, pos_order.amount_total)
        vat_amount = max(0.0, pos_order.amount_tax)
        known_before_discount = (
            items_subtotal
            + totals["delivery_fee"]
            + totals["service_fee"]
            + totals["tip_amount"]
            + vat_amount
        )
        balancing_discount = max(0.0, known_before_discount - amount_total)
        discount_amount = max(explicit_discount, balancing_discount)
        unexplained_positive = amount_total - (known_before_discount - discount_amount)
        if unexplained_positive > 0 and not currency.is_zero(unexplained_positive):
            # Legacy connectors sometimes collapse delivery, small-order and
            # rounding adjustments into one POS line.  Preserve the amount in
            # the generic service-fee bucket instead of silently losing it.
            totals["service_fee"] += unexplained_positive

        commission_amount = self._legacy_optional_amount(
            pos_order,
            ("aggregator_commission_amount", "platform_commission_amount"),
        )
        if commission_amount is None:
            commission_rate = max(0.0, connection.commission_pct)
            commission_amount = amount_total * commission_rate / 100.0
        else:
            commission_amount = max(0.0, commission_amount)
            commission_rate = (
                commission_amount / amount_total * 100.0 if amount_total else 0.0
            )
        net_amount = self._legacy_optional_amount(
            pos_order,
            ("net_to_restaurant", "aggregator_net_amount", "platform_net_amount"),
        )
        if net_amount is None:
            net_amount = amount_total - commission_amount
        return {
            "items_subtotal": currency.round(max(0.0, items_subtotal)),
            "discount_amount": currency.round(max(0.0, discount_amount)),
            "delivery_fee": currency.round(totals["delivery_fee"]),
            "service_fee": currency.round(totals["service_fee"]),
            "tip_amount": currency.round(totals["tip_amount"]),
            "vat_amount": currency.round(vat_amount),
            "amount_total": currency.round(amount_total),
            "commission_rate": min(100.0, max(0.0, commission_rate)),
            "commission_amount": currency.round(max(0.0, commission_amount)),
            "net_amount": currency.round(max(0.0, net_amount)),
        }

    @staticmethod
    def _legacy_optional_amount(pos_order, field_names):
        for field_name in field_names:
            if field_name in pos_order._fields and pos_order[field_name] is not False:
                try:
                    return float(pos_order[field_name])
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _legacy_operational_products(store):
        if not store:
            return {}
        return {
            product.id: kind
            for product, kind in (
                (store.delivery_fee_product_id, "delivery_fee"),
                (store.service_fee_product_id, "service_fee"),
                (store.tip_product_id, "tip_amount"),
            )
            if product
        }

    @api.model
    def _legacy_driver_values(self, pos_order):
        values = {
            "driver_name": self._legacy_optional_text(
                pos_order, ("driver_name", "rider_name", "courier_name")
            ),
            "driver_phone": self._legacy_optional_text(
                pos_order, ("driver_phone", "rider_phone", "courier_phone")
            ),
            "driver_tracking_url": self._legacy_optional_text(
                pos_order, ("driver_tracking_url", "tracking_url")
            ),
        }
        eta = self._legacy_optional_amount(
            pos_order, ("driver_eta_minutes", "rider_eta_minutes")
        )
        if eta is not None:
            values["driver_eta_minutes"] = max(0, int(eta))

        payload = self._legacy_payload(pos_order)
        candidates = [payload]
        for key in ("payload", "order", "details", "delivery"):
            nested = payload.get(key) if isinstance(payload, dict) else None
            if isinstance(nested, dict):
                candidates.append(nested)
                details = nested.get("details")
                if isinstance(details, dict):
                    candidates.append(details)
        driver = next(
            (
                candidate[key]
                for candidate in candidates
                for key in ("driver", "rider", "courier", "delivery_driver")
                if isinstance(candidate.get(key), dict)
            ),
            {},
        )
        values["driver_name"] = values["driver_name"] or self._dictionary_text(
            driver, ("name", "full_name", "driver_name")
        )
        values["driver_phone"] = values["driver_phone"] or self._dictionary_text(
            driver, ("phone", "mobile", "phone_number")
        )
        values["driver_tracking_url"] = values[
            "driver_tracking_url"
        ] or self._dictionary_text(driver, ("tracking_url", "tracking_link"))
        if "driver_eta_minutes" not in values:
            eta_value = self._dictionary_text(
                driver, ("eta_minutes", "eta", "estimated_arrival_minutes")
            )
            try:
                if eta_value is not False:
                    values["driver_eta_minutes"] = max(0, int(float(eta_value)))
            except (TypeError, ValueError):
                pass
        return values

    @staticmethod
    def _legacy_optional_text(pos_order, field_names):
        for field_name in field_names:
            if field_name in pos_order._fields:
                value = str(pos_order[field_name] or "").strip()
                if value:
                    return value
        return False

    @staticmethod
    def _dictionary_text(values, keys):
        for key in keys:
            value = values.get(key) if isinstance(values, dict) else None
            if value not in (None, False, ""):
                return str(value).strip() or False
        return False

    @staticmethod
    def _legacy_payload(pos_order):
        for field_name in ("feedus_payload_json", "blend_payload"):
            if field_name not in pos_order._fields or not pos_order[field_name]:
                continue
            value = pos_order[field_name]
            if isinstance(value, dict):
                return value
            try:
                decoded = json.loads(value)
            except (TypeError, ValueError):
                continue
            if isinstance(decoded, dict):
                return decoded
        return {}

    @staticmethod
    def _legacy_scheduled_at(pos_order):
        for field_name in ("feedus_schedule_datetime", "preset_time"):
            if field_name in pos_order._fields and pos_order[field_name]:
                return pos_order[field_name]
        return False

    @staticmethod
    def _legacy_payment_method(pos_order):
        for field_name in ("feedus_payment_type",):
            if field_name in pos_order._fields and pos_order[field_name]:
                return str(pos_order[field_name])
        return False

    @staticmethod
    def _legacy_fulfillment_type(pos_order):
        if "delivery_type" in pos_order._fields and pos_order.delivery_type:
            return {"delivery": "delivery", "pickup": "pickup", "dinein": "dine_in"}.get(
                pos_order.delivery_type, "delivery"
            )
        return "delivery"

    @api.model
    def get_pos_board_data(self, provider_id, pos_config_id):
        config = self._check_pos_board_access(pos_config_id)
        try:
            provider_key = int(provider_id or 0)
        except (TypeError, ValueError):
            provider_key = 0
        provider = self.env["bs.delivery.provider"].sudo().search(
            [
                ("id", "=", provider_key),
                ("active", "=", True),
                ("connector_kind", "in", ["aggregator", "hybrid"]),
            ],
            limit=1,
        )
        if not provider:
            return {"provider": {}, "orders": []}
        connections = self.env["bs.delivery.connection"]._for_pos_board(
            config,
            provider=provider,
        )
        stores = self.env["bs.delivery.store.binding"].sudo().search(
            [
                ("connection_id", "in", connections.ids),
                ("pos_config_id", "=", config.id),
                ("active", "=", True),
            ]
        )
        order_model = self.sudo()
        orders = order_model.search(
            [
                ("provider_id", "=", provider.id),
                ("connection_id", "in", connections.ids),
                ("company_id", "=", config.company_id.id),
                ("pos_config_id", "=", config.id),
                ("state", "in", ["received", "accepted", "preparing", "ready", "picked_up"]),
            ],
            order="received_at, id",
        )
        stores_by_connection = defaultdict(
            lambda: self.env["bs.delivery.store.binding"].browse()
        )
        for store in stores:
            stores_by_connection[store.connection_id.id] |= store
        route_states = []
        route_sound_settings = []
        for connection in connections:
            connection_paused = connection.is_receiving_paused()
            connection_stores = stores_by_connection[connection.id]
            if connection_stores:
                for store in connection_stores:
                    route_states.append(connection_paused or store.is_paused)
                    route_sound_settings.append(bool(store.sound_alert))
            else:
                route_states.append(connection_paused)
                route_sound_settings.append(bool(connection.sound_alert))
        reject_reasons = self.env["bs.delivery.reject.reason"].sudo().search(
            [
                ("active", "=", True),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", config.company_id.id),
                "|",
                ("provider_id", "=", False),
                ("provider_id", "=", provider.id),
            ]
        )
        return {
            "provider": {
                "id": provider.id,
                "name": provider.display_name,
                "code": provider.code,
                "color": provider.color,
                "image_url": provider._pos_logo_data_url(),
                "paused": bool(route_states) and all(route_states),
                "partially_paused": bool(route_states)
                and any(route_states)
                and not all(route_states),
                "sound_alert": any(route_sound_settings),
                "polling_interval_minutes": min(
                    connections.mapped("polling_interval_minutes") or [2]
                ),
            },
            "orders": [order._pos_board_values() for order in orders],
            "rejection_reasons": [
                {
                    "id": reason.id,
                    "name": reason.display_name,
                    "code": reason.code,
                    "external_code": reason.external_code or reason.code,
                    "requires_note": reason.requires_note,
                }
                for reason in reject_reasons
            ],
        }

    def _pos_board_values(self):
        self.ensure_one()
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "provider_id": self.provider_id.id,
            "provider_name": self.provider_id.display_name,
            "provider_reference": self.external_reference or self.external_order_id,
            "customer_name": self.customer_name,
            "needs_invoice_partner": self._needs_manual_invoice_customer(),
            "customer_phone_masked": self.customer_phone_masked,
            "delivery_address": self.delivery_address,
            "driver_name": self.driver_name,
            "driver_phone": self.driver_phone,
            "driver_eta_minutes": self.driver_eta_minutes,
            "fulfillment_type": self.fulfillment_type,
            "scheduled_at": fields.Datetime.to_string(self.scheduled_at),
            "received_at": fields.Datetime.to_string(self.received_at),
            "age_seconds": self.age_seconds,
            "customer_note": self.customer_note,
            "is_prepaid": self.is_prepaid,
            "amount_total": self.amount_total,
            "currency_code": self.currency_id.name,
            "currency_symbol": self.currency_id.symbol,
            "sound_alert": self._pos_notification_values()["sound_alert"],
        }

    def _pos_notification_values(self):
        """Small, stable payload shared by bus events and polling fallback."""
        self.ensure_one()
        store = self.store_binding_id
        sound_alert = (
            bool(store.sound_alert)
            if store
            else bool(self.connection_id.sound_alert)
        )
        return {
            "order_id": self.id,
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "provider_id": self.provider_id.id,
            "provider_name": self.provider_id.display_name,
            "provider_reference": self.external_reference or self.external_order_id,
            "received_at": fields.Datetime.to_string(self.received_at),
            "sound_alert": sound_alert,
        }

    @api.model
    def action_pos_transition(
        self,
        order_id,
        target_state,
        reason=None,
        reject_reason_id=None,
        provider_id=None,
        pos_config_id=None,
        invoice_partner_id=None,
    ):
        config = self._check_pos_board_access(pos_config_id)
        try:
            order_key = int(order_id or 0)
        except (TypeError, ValueError):
            order_key = 0
        order = self.sudo().search(
            [
                ("id", "=", order_key),
                ("company_id", "=", config.company_id.id),
                ("pos_config_id", "=", config.id),
            ],
            limit=1,
        )
        if not order:
            raise UserError(_("The delivery order no longer exists."))
        try:
            provider_key = int(provider_id or 0)
        except (TypeError, ValueError):
            provider_key = 0
        if provider_id and order.provider_id.id != provider_key:
            raise AccessError(_("This delivery order belongs to another provider."))
        if target_state == "accepted":
            if invoice_partner_id:
                order._set_delivery_invoice_partner(invoice_partner_id)
            if order._needs_manual_invoice_customer():
                raise UserError(_("Choose an invoice customer before accepting the delivery order."))
        order._with_delivery_actor(self.env.user).transition_to(
            target_state,
            reason=str(reason or "").strip()[:2000] or False,
            reject_reason_id=reject_reason_id,
            source="manual",
            expected_state=order.state,
            idempotency_key="pos:%s:%s:%s"
            % (order.id, target_state, uuid.uuid4()),
        )
        return self.get_pos_board_data(order.provider_id.id, order.pos_config_id.id)

    def _check_pos_board_access(self, pos_config_id):
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessError(_("Point of Sale access is required."))
        try:
            config_id = int(pos_config_id or 0)
        except (TypeError, ValueError):
            config_id = 0
        config = self.env["pos.config"].browse(config_id).exists()
        if config:
            config.check_access("read")
        if not config or config.company_id not in self.env.companies:
            raise AccessError(_("The Point of Sale branch is not available."))
        if not self.env.user.has_group("point_of_sale.group_pos_manager"):
            session = config.current_session_id
            if not session or session.user_id != self.env.user:
                raise AccessError(
                    _("You can only operate the Point of Sale branch opened by your user.")
                )
        return config

    def _notify_pos_board(self, event):
        for order in self:
            # POS DataService listens on the private access-token channel of
            # its ``pos.config``.  Using pos.config._notify is essential here:
            # a plain bus channel has a different event name and would leave
            # cashiers dependent on polling instead of receiving the sound and
            # popup immediately.
            config = order.pos_config_id
            if not config:
                continue
            config.sudo()._notify(
                "BS_DELIVERY_POS_BOARD",
                {
                    "event": event,
                    **order._pos_notification_values(),
                },
            )
        return True

    def _kitchen_autoprint_enabled(self):
        self.ensure_one()
        return bool(
            self.connection_id.auto_print_kitchen
            or self.store_binding_id.auto_print_kitchen
        )

    def _is_kitchen_manual_retry_requested(self):
        self.ensure_one()
        return bool(self.kitchen_manual_retry_requested)

    def _kitchen_print_is_stale(self, now=None):
        self.ensure_one()
        # The ticket ages from the moment it was requested, i.e. once its POS
        # order exists. An order accepted while the branch POS was closed gets
        # its POS order (and its print request) only when the session opens;
        # measuring from accepted_at expired those tickets before any print.
        started_at = (
            self.kitchen_print_requested_at or self.accepted_at or self.create_date
        )
        return bool(
            started_at
            and started_at < fields.Datetime.subtract(
                now or fields.Datetime.now(),
                minutes=KITCHEN_AUTO_PRINT_MAX_AGE_MINUTES,
            )
        )

    def _expire_stale_kitchen_print(self):
        self.ensure_one()
        self.sudo()._with_delivery_internal_update().write({
            "kitchen_print_state": "error",
            "kitchen_print_error": _(
                "The kitchen print request is over 15 minutes old. "
                "Review the order and retry printing manually."
            ),
            "kitchen_manual_retry_requested": False,
            "kitchen_print_claim_key": False,
            "kitchen_print_claim_until": False,
        })

    def _request_kitchen_autoprint(self):
        for order in self:
            if (
                not order.pos_order_id
                or not order._kitchen_autoprint_enabled()
                or order.kitchen_print_state != "not_requested"
            ):
                continue
            now = fields.Datetime.now()
            order.sudo()._with_delivery_internal_update().write(
                {
                    "kitchen_print_state": "pending",
                    "kitchen_print_requested_at": (
                        order.kitchen_print_requested_at or now
                    ),
                    "kitchen_print_error": False,
                    "kitchen_print_claim_key": False,
                    "kitchen_print_claim_until": False,
                }
            )
            order._dispatch_kitchen_print_job()
        return True

    def action_retry_kitchen_print(self):
        """Request one further attempt after a manager checks the print result."""
        self.check_access("write")
        if not self.env.user.has_group("bs_pos_delivery_core.group_delivery_manager"):
            raise AccessError(_("Only a delivery manager can retry kitchen printing."))
        if self.filtered(lambda order: order.company_id not in self.env.companies):
            raise AccessError(_("The delivery order belongs to an unavailable company."))
        for order in self.sudo():
            self.env.cr.execute(
                "SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE", [order.id],
            )
            order.invalidate_recordset()
            if not order.kitchen_can_retry or not order._kitchen_autoprint_enabled():
                raise UserError(_("This order is not eligible for a kitchen print retry."))
            if (order.kitchen_print_claim_until
                    and order.kitchen_print_claim_until > fields.Datetime.now()):
                raise UserError(_("The POS device is still handling this kitchen ticket."))
            order._with_delivery_internal_update().write({
                "kitchen_print_state": "pending",
                "kitchen_manual_retry_requested": True,
                "kitchen_print_requested_at": fields.Datetime.now(),
                "kitchen_print_error": False,
                "kitchen_print_claim_key": False,
                "kitchen_print_claim_until": False,
            })
            order._dispatch_kitchen_print_job()
            order._with_delivery_internal_update().write({
                "kitchen_print_last_attempt_at": False,
            })
        return True

    def _dispatch_kitchen_print_job(self):
        """Synchronize the POS order and wake an open cashier device."""
        self.ensure_one()
        pos_order = self.pos_order_id
        session = pos_order.session_id
        config = session.config_id
        if not pos_order or not session or not config:
            return False
        products = pos_order.lines.product_id
        records = {
            # POS consumes the static payload before it fetches missing
            # relations. Hidden discount and modifier products are not in its
            # initial catalogue, so load them before the order lines.
            "product.template": products.product_tmpl_id.ids,
            "product.product": products.ids,
            "pos.order": pos_order.ids,
            "pos.order.line": pos_order.lines.ids,
        }
        if pos_order.payment_ids:
            records["pos.payment"] = pos_order.payment_ids.ids
        config.sudo().notify_synchronisation(session.id, 0, records)
        # Do NOT stamp kitchen_print_last_attempt_at here: dispatching is not
        # a print attempt.  Stamping it hid the job from
        # get_pending_kitchen_prints() for 30 seconds, so the POS that
        # received this very notification found nothing to print and the
        # ticket waited for the next poll (about one minute).  Only a POS
        # claim records an attempt.
        #
        # The payload carries the complete job so the POS can claim and print
        # immediately, without an extra round trip.  The claim lease still
        # guarantees a single physical ticket across several POS devices.
        config.sudo()._notify(
            "BS_DELIVERY_POS_BOARD",
            {
                "event": "kitchen_auto_print",
                "auto_print": True,
                "order_id": self.id,
                "pos_order_id": pos_order.id,
                "provider_id": self.provider_id.id,
                "provider_name": self.provider_id.display_name,
                "kitchen_job": self._kitchen_print_job_values(),
            },
        )
        return True

    def _kitchen_print_job_values(self):
        self.ensure_one()
        return {
            "order_id": self.id,
            "pos_order_id": self.pos_order_id.id,
            "provider_id": self.provider_id.id,
            "provider_name": self.provider_id.display_name,
            "attempt": self.kitchen_print_attempts,
        }

    @api.model
    def get_pending_kitchen_prints(self, pos_config_id, limit=20):
        """Return only unattempted or manager-requested kitchen jobs.

        There is deliberately no time throttle: a pending, unclaimed job is
        returned at once.  Duplicate printing is prevented by the claim lease
        (claim_kitchen_print), not by hiding the job.
        """
        config = self._check_pos_board_access(pos_config_id)
        try:
            row_limit = min(max(int(limit or 20), 1), 50)
        except (TypeError, ValueError):
            row_limit = 20
        candidates = self.sudo().search(
            [
                ("company_id", "=", config.company_id.id),
                ("pos_config_id", "=", config.id),
                ("pos_order_id", "!=", False),
                ("state", "in", ["accepted", "preparing", "ready"]),
                ("kitchen_print_state", "=", "pending"),
                "|",
                ("kitchen_print_attempts", "=", 0),
                ("kitchen_manual_retry_requested", "=", True),
                "|",
                ("connection_id.auto_print_kitchen", "=", True),
                ("store_binding_id.auto_print_kitchen", "=", True),
                "|",
                ("kitchen_print_claim_until", "=", False),
                ("kitchen_print_claim_until", "<=", fields.Datetime.now()),
            ],
            order="kitchen_print_requested_at, id",
            limit=row_limit,
        )
        if not candidates:
            return []
        return self._dispatch_pending_kitchen_jobs(config, candidates)

    def _dispatch_pending_kitchen_jobs(self, config, candidates):
        """Lock, expire old tickets, and dispatch only current print jobs."""
        self.env.cr.execute(
            "SELECT id FROM bs_delivery_order WHERE id IN %s "
            "ORDER BY kitchen_print_requested_at, id FOR UPDATE SKIP LOCKED",
            [tuple(candidates.ids)],
        )
        locked_ids = [row[0] for row in self.env.cr.fetchall()]
        claimed = candidates.filtered(lambda order: order.id in locked_ids)
        jobs = []
        for order in claimed:
            order.invalidate_recordset([
                "state", "pos_order_id", "pos_config_id", "kitchen_print_state",
                "kitchen_print_attempts", "kitchen_print_requested_at",
                "kitchen_manual_retry_requested", "accepted_at",
            ])
            if (
                order.state not in ("accepted", "preparing", "ready")
                or order.pos_config_id != config
                or not order.pos_order_id
                or order.kitchen_print_state != "pending"
                or (order.kitchen_print_attempts
                    and not order._is_kitchen_manual_retry_requested())
                or not order._kitchen_autoprint_enabled()
            ):
                continue
            if order._kitchen_print_is_stale():
                order._expire_stale_kitchen_print()
                continue
            # The requesting POS loads the order itself, so polling does not
            # re-broadcast the job.  A broadcast here made every open POS
            # poll again on each poll, a self-feeding notification loop.
            jobs.append(order._kitchen_print_job_values())
        return jobs

    @api.model
    def claim_kitchen_print(self, order_id, pos_config_id, claim_key):
        """Lease one job to one POS tab so multiple tills cannot print it twice."""
        config = self._check_pos_board_access(pos_config_id)
        try:
            order_key = int(order_id or 0)
        except (TypeError, ValueError):
            order_key = 0
        normalized_claim = str(claim_key or "").strip()[:128]
        if len(normalized_claim) < 16:
            raise ValidationError(_("A valid kitchen print claim key is required."))
        order = self.sudo().search(
            [
                ("id", "=", order_key),
                ("company_id", "=", config.company_id.id),
                ("pos_config_id", "=", config.id),
            ],
            limit=1,
        )
        if not order:
            raise AccessError(_("The kitchen print job is not available."))
        self.env.cr.execute(
            "SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE",
            [order.id],
        )
        order.invalidate_recordset(
            ["kitchen_print_state", "kitchen_print_attempts",
             "kitchen_manual_retry_requested", "kitchen_print_claim_key",
             "kitchen_print_claim_until", "kitchen_print_requested_at",
             "accepted_at"]
        )
        now = fields.Datetime.now()
        if order.kitchen_print_state == "sent":
            return False
        if (
            not order.pos_order_id
            or order.state not in ("accepted", "preparing", "ready")
            or order.kitchen_print_state != "pending"
            or (order.kitchen_print_attempts
                and not order._is_kitchen_manual_retry_requested())
            or not order._kitchen_autoprint_enabled()
        ):
            return False
        if order._kitchen_print_is_stale(now):
            order._expire_stale_kitchen_print()
            return False
        if (
            order.kitchen_print_claim_until
            and order.kitchen_print_claim_until > now
            and order.kitchen_print_claim_key != normalized_claim
        ):
            return False
        if (
            order.kitchen_print_claim_key == normalized_claim
            and order.kitchen_print_claim_until
            and order.kitchen_print_claim_until > now
        ):
            return True
        order.sudo()._with_delivery_internal_update().write(
            {
                "kitchen_print_state": "attempting",
                "kitchen_manual_retry_requested": False,
                "kitchen_print_claim_key": normalized_claim,
                "kitchen_print_claim_until": fields.Datetime.add(now, seconds=120),
                "kitchen_print_last_attempt_at": now,
                "kitchen_print_attempts": order.kitchen_print_attempts + 1,
            }
        )
        return True

    @api.model
    def acknowledge_kitchen_print(
        self,
        order_id,
        pos_config_id,
        claim_key,
        success,
        error=None,
    ):
        config = self._check_pos_board_access(pos_config_id)
        try:
            order_key = int(order_id or 0)
        except (TypeError, ValueError):
            order_key = 0
        order = self.sudo().search(
            [
                ("id", "=", order_key),
                ("company_id", "=", config.company_id.id),
                ("pos_config_id", "=", config.id),
            ],
            limit=1,
        )
        if not order:
            raise AccessError(_("The kitchen print job is not available."))
        normalized_claim = str(claim_key or "").strip()[:128]
        if len(normalized_claim) < 16:
            raise ValidationError(_("A valid kitchen print claim key is required."))
        if not isinstance(success, bool):
            raise ValidationError(_("A boolean kitchen print result is required."))
        self.env.cr.execute(
            "SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE",
            [order.id],
        )
        order.invalidate_recordset(
            ["kitchen_print_state", "kitchen_print_claim_key", "kitchen_print_claim_until"]
        )
        if (
            order.kitchen_print_state != "sent"
            and order.kitchen_print_claim_key != normalized_claim
        ):
            raise AccessError(_("This kitchen print job is leased by another POS device."))
        if order.kitchen_print_state == "sent":
            return True
        if success:
            order.sudo()._with_delivery_internal_update().write(
                {
                    "kitchen_print_state": "sent",
                    "kitchen_print_sent_at": fields.Datetime.now(),
                    "kitchen_print_error": False,
                    "kitchen_print_claim_key": False,
                    "kitchen_print_claim_until": False,
                }
            )
        elif order.kitchen_print_state != "sent":
            order.sudo()._with_delivery_internal_update().write(
                {
                    "kitchen_print_state": "error",
                    "kitchen_print_error": str(error or _("Unknown print error"))[:2000],
                    "kitchen_print_claim_key": False,
                    "kitchen_print_claim_until": False,
                }
            )
        return True


class DeliveryStoreOperationalConfiguration(models.Model):
    _inherit = "bs.delivery.store.binding"

    warehouse_id = fields.Many2one("stock.warehouse", check_company=True)
    pricelist_id = fields.Many2one("product.pricelist", check_company=True)
    fiscal_position_id = fields.Many2one("account.fiscal.position", check_company=True)
    timezone = fields.Selection(selection=lambda self: self._tz_get(), default=lambda self: self.env.user.tz or "UTC")
    currency_id = fields.Many2one(
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    sound_alert = fields.Boolean(default=True)
    auto_print_kitchen = fields.Boolean(default=False)
    late_threshold_minutes = fields.Integer(default=15, required=True)
    delivery_fee_product_id = fields.Many2one("product.product", check_company=True)
    service_fee_product_id = fields.Many2one("product.product", check_company=True)
    adjustment_product_id = fields.Many2one("product.product", check_company=True)
    tip_product_id = fields.Many2one("product.product", check_company=True)
    default_payment_method_id = fields.Many2one("pos.payment.method", check_company=True)
    available_preset_ids = fields.Many2many(
        related="pos_config_id.available_preset_ids",
        string="POS Available Presets",
    )
    delivery_preset_id = fields.Many2one(
        "pos.preset", string="Delivery Preset", ondelete="set null",
        help="POS preset for every incoming platform order (shown under the POS Delivery tab), "
             "unless a Pickup or Dine In preset is set below.",
    )
    pickup_preset_id = fields.Many2one(
        "pos.preset", string="Pickup Preset", ondelete="set null",
        help="Optional POS preset for incoming pickup orders. Leave empty to use the Delivery Preset.",
    )
    dine_in_preset_id = fields.Many2one(
        "pos.preset", string="Dine In Preset", ondelete="set null",
        help="Optional POS preset for incoming dine-in orders. Leave empty to use the Delivery Preset.",
    )

    def _auto_delivery_preset(self):
        """The branch's Delivery preset when none is selected on the mapping.

        Without it a platform order fell back to the POS default preset
        (usually "Dine In"), on the POS tabs and on the kitchen ticket.
        """
        self.ensure_one()
        config = self.pos_config_id
        if not config.use_presets:
            return self.env["pos.preset"]
        presets = config.available_preset_ids.filtered(lambda preset: not preset.is_return)
        standard = self.env.ref("pos_restaurant.pos_delivery_preset", raise_if_not_found=False)
        for candidate in (
            presets.filtered(lambda preset: getattr(preset, "service_at", False) == "delivery"),
            presets.filtered(lambda preset: standard and preset == standard),
            presets.filtered(lambda preset: preset.identification == "address"),
            presets.filtered(lambda preset: any(
                word in (preset.with_context(lang="en_US").name or "").lower()
                or word in (preset.name or "")
                for word in ("delivery", "توصيل")
            )),
        ):
            if candidate:
                return candidate.sorted("id")[:1]
        return self.env["pos.preset"]

    def _delivery_preset_for(self, fulfillment_type):
        self.ensure_one()
        # Every aggregator order lands under the Delivery preset unless its
        # fulfillment type has a preset of its own on this branch.
        return {
            "pickup": self.pickup_preset_id,
            "dine_in": self.dine_in_preset_id,
        }.get(fulfillment_type) or self.delivery_preset_id

    def _is_usable_delivery_preset(self, preset):
        self.ensure_one()
        config = self.pos_config_id
        return bool(
            preset
            and config.use_presets
            and preset in config.available_preset_ids
            and not preset.is_return
        )

    @api.constrains("pos_config_id", "delivery_preset_id", "pickup_preset_id", "dine_in_preset_id")
    def _check_delivery_presets(self):
        for store in self:
            for preset in (store.delivery_preset_id, store.pickup_preset_id,
                           store.dine_in_preset_id):
                if preset and not store._is_usable_delivery_preset(preset):
                    raise ValidationError(_(
                        "Preset '%(preset)s' must be an available, non-return preset on "
                        "POS branch '%(branch)s', with Use Presets enabled.",
                        preset=preset.display_name,
                        branch=store.pos_config_id.display_name,
                    ))

    @api.model
    def _tz_get(self):
        return [(tz, tz) for tz in pytz.all_timezones]

    @api.constrains("late_threshold_minutes")
    def _check_late_threshold(self):
        for store in self:
            if store.late_threshold_minutes <= 0:
                raise ValidationError(_("The late threshold must be greater than zero."))


class DeliveryRejectWizard(models.TransientModel):
    _name = "bs.delivery.reject.wizard"
    _description = "Reject Delivery Order"

    order_id = fields.Many2one("bs.delivery.order", required=True, readonly=True)
    company_id = fields.Many2one(related="order_id.company_id", readonly=True)
    provider_id = fields.Many2one(related="order_id.provider_id", readonly=True)
    reject_reason_id = fields.Many2one(
        "bs.delivery.reject.reason",
        required=True,
        domain="[('active', '=', True), '|', ('company_id', '=', False), ('company_id', '=', company_id), '|', ('provider_id', '=', False), ('provider_id', '=', provider_id)]",
    )
    note = fields.Text()

    def action_reject(self):
        self.ensure_one()
        self.order_id._manual_transition(
            "rejected",
            reason=self.note,
            reject_reason_id=self.reject_reason_id,
        )
        return {"type": "ir.actions.act_window_close"}


class DeliveryAcceptCustomerWizard(models.TransientModel):
    _name = "bs.delivery.accept.customer.wizard"
    _description = "Choose Delivery Invoice Customer"

    order_id = fields.Many2one("bs.delivery.order", required=True, readonly=True)
    company_id = fields.Many2one(related="order_id.company_id", readonly=True)
    partner_id = fields.Many2one(
        "res.partner", string="Invoice Customer", required=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )

    def action_confirm(self):
        self.ensure_one()
        order = self.order_id
        if not self.env.user.has_group("bs_pos_delivery_core.group_delivery_operator"):
            raise AccessError(_("Delivery operator access is required."))
        order.check_access("write")
        if order.company_id not in self.env.companies:
            raise AccessError(_("The delivery order belongs to an unavailable company."))
        if order.state != "received":
            raise UserError(_("Only a received delivery order can be accepted."))
        order._set_delivery_invoice_partner(self.partner_id.id)
        order._manual_transition("accepted")
        return {"type": "ir.actions.act_window_close"}
