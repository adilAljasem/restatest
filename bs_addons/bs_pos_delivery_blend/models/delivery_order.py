"""Finalize Blend financial orders through the canonical Core acceptance."""

import json
import logging
import uuid
from decimal import Decimal, InvalidOperation

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.bs_pos_delivery_core.exceptions import DeliveryAuthenticationError


_logger = logging.getLogger(__name__)


class DeliveryOrder(models.Model):
    _inherit = "bs.delivery.order"

    blend_kitchen_manual_retry_requested = fields.Boolean(
        readonly=True, copy=False,
    )
    blend_kitchen_can_retry = fields.Boolean(
        compute="_compute_blend_kitchen_can_retry",
    )

    @api.depends("provider_id.code", "state", "pos_order_id", "kitchen_print_state",
                 "kitchen_print_attempts")
    def _compute_kitchen_can_retry(self):
        super()._compute_kitchen_can_retry()
        for order in self.filtered(lambda row: row.provider_id.code == "blend"):
            order.kitchen_can_retry = False

    def _is_kitchen_manual_retry_requested(self):
        self.ensure_one()
        return (super()._is_kitchen_manual_retry_requested()
                or bool(self.blend_kitchen_manual_retry_requested))

    @api.depends("provider_id.code", "state", "pos_order_id", "kitchen_print_state")
    def _compute_blend_kitchen_can_retry(self):
        for order in self:
            order.blend_kitchen_can_retry = bool(
                order.provider_id.code == "blend"
                and order.pos_order_id
                and order.state in ("accepted", "preparing", "ready")
                and order.kitchen_print_state in ("error", "attempting")
            )

    def _request_kitchen_autoprint(self):
        # An accepted Blend order gets one automatic request, even if its
        # kitchen printer later fails or its acknowledgement never arrives.
        for order in self:
            if order.provider_id.code != "blend" or order.kitchen_print_state == "not_requested":
                super(DeliveryOrder, order)._request_kitchen_autoprint()
        return True

    @api.model
    def get_pending_kitchen_prints(self, pos_config_id, limit=20):
        """Dispatch only unattempted or manager-requested kitchen jobs."""
        config = self._check_pos_board_access(pos_config_id)
        try:
            row_limit = min(max(int(limit or 20), 1), 50)
        except (TypeError, ValueError):
            row_limit = 20
        now = fields.Datetime.now()
        # No 30-second throttle: it hid freshly accepted orders from the POS
        # that was notified about them.  The claim lease prevents duplicates.
        candidates = self.sudo().search([
            ("company_id", "=", config.company_id.id),
            ("pos_config_id", "=", config.id),
            ("pos_order_id", "!=", False),
            ("state", "in", ["accepted", "preparing", "ready"]),
            ("kitchen_print_state", "=", "pending"),
            "|",
            ("kitchen_print_attempts", "=", 0),
            "|",
            ("kitchen_manual_retry_requested", "=", True),
            ("blend_kitchen_manual_retry_requested", "=", True),
            "|",
            ("connection_id.auto_print_kitchen", "=", True),
            ("store_binding_id.auto_print_kitchen", "=", True),
            "|",
            ("kitchen_print_claim_until", "=", False),
            ("kitchen_print_claim_until", "<=", now),
        ], order="kitchen_print_requested_at, id", limit=row_limit)
        if not candidates:
            return []
        return self._dispatch_pending_kitchen_jobs(config, candidates)

    def _expire_stale_kitchen_print(self):
        result = super()._expire_stale_kitchen_print()
        if self.provider_id.code == "blend":
            self.sudo()._with_delivery_internal_update().write({
                "blend_kitchen_manual_retry_requested": False,
            })
        return result

    @api.model
    def claim_kitchen_print(self, order_id, pos_config_id, claim_key):
        config = self._check_pos_board_access(pos_config_id)
        if len(str(claim_key or "").strip()[:128]) < 16:
            raise ValidationError(_("A valid kitchen print claim key is required."))
        try:
            order_key = int(order_id or 0)
        except (TypeError, ValueError):
            order_key = 0
        order = self.sudo().search([
            ("id", "=", order_key),
            ("company_id", "=", config.company_id.id),
            ("pos_config_id", "=", config.id),
        ], limit=1)
        if not order:
            raise AccessError(_("The kitchen print job is not available."))
        if order.provider_id.code != "blend":
            return super().claim_kitchen_print(order_id, pos_config_id, claim_key)
        self.env.cr.execute(
            "SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE", [order.id],
        )
        order.invalidate_recordset([
            "kitchen_print_state", "kitchen_print_attempts",
            "kitchen_manual_retry_requested", "blend_kitchen_manual_retry_requested",
        ])
        if (order.kitchen_print_state != "pending"
                or (order.kitchen_print_attempts
                    and not order._is_kitchen_manual_retry_requested())):
            return False
        if not super().claim_kitchen_print(order_id, pos_config_id, claim_key):
            return False
        order.sudo()._with_delivery_internal_update().write({
            "kitchen_print_state": "attempting",
            "blend_kitchen_manual_retry_requested": False,
        })
        return True

    def action_retry_blend_kitchen_print(self):
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
            if not order.blend_kitchen_can_retry:
                raise UserError(_("This Blend order is not eligible for a kitchen print retry."))
            if (order.kitchen_print_claim_until
                    and order.kitchen_print_claim_until > fields.Datetime.now()):
                raise UserError(_("The POS device is still handling this kitchen ticket."))
            order._with_delivery_internal_update().write({
                "kitchen_print_state": "pending",
                "blend_kitchen_manual_retry_requested": True,
                "kitchen_print_requested_at": fields.Datetime.now(),
                "kitchen_print_error": False,
                "kitchen_print_claim_key": False,
                "kitchen_print_claim_until": False,
            })
            order._dispatch_kitchen_print_job()
            # The manual request is ready now, not after Core's 30-second
            # automatic retry throttle.
            order._with_delivery_internal_update().write({
                "kitchen_print_last_attempt_at": False,
            })
        return True

    def _link_pos_order_modifiers_and_combos(self, pos_order, delivery_pos_line_map):
        result = super()._link_pos_order_modifiers_and_combos(
            pos_order, delivery_pos_line_map,
        )
        if self.provider_id.code != "blend" or self.contract_version != 1:
            return result
        for line in self.line_ids.filtered("is_modifier"):
            pos_line = delivery_pos_line_map.get(line.id)
            if not pos_line or not pos_line.modifier_option_id:
                continue
            if not pos_line.modifier_option_id.group_id.included_qty:
                pos_line.modifier_is_included = False
        return result

    blend_api_order_id = fields.Char(
        string="Blend API Order ID", copy=False, readonly=True, index=True,
        default=lambda self: self.env.context.get("bs_blend_api_order_id"),
    )

    _blend_api_order_id_unique = models.Constraint(
        "unique (blend_api_order_id)", "The Blend API order ID must be unique.",
    )

    def _pos_order_line_price(self, line, taxes):
        if (self.provider_id.code == "blend" and self.contract_version == 1
                and not line.discount_amount and taxes):
            effective_taxes = self.store_binding_id.fiscal_position_id.map_tax(taxes)
            if all(tax.price_include for tax in effective_taxes):
                # Retain the provider's gross item total while Odoo allocates
                # the global tax rounding cent across the order's lines.
                return (line.price_subtotal + line.vat_amount) / line.quantity
        return super()._pos_order_line_price(line, taxes)

    def _finalize_accepted_pos_order(self):
        try:
            return super()._finalize_accepted_pos_order()
        except UserError as error:
            if (self.provider_id.code != "blend" or self.contract_version != 1
                    or str(error) != _("The delivery order was not fully paid and invoiced.")):
                raise
            pos_order = self.pos_order_id
            invoice = pos_order.account_move
            failed = []
            if pos_order.state != "done":
                failed.append("pos_state")
            if not invoice or invoice.state != "posted":
                failed.append("invoice_state")
            if invoice:
                if self.currency_id.compare_amounts(invoice.amount_total, self.amount_total):
                    failed.append("invoice_total")
                if self.currency_id.compare_amounts(invoice.amount_tax, self.vat_amount):
                    failed.append("invoice_tax")
                if not self.currency_id.is_zero(invoice.amount_residual):
                    failed.append("invoice_residual")
            _logger.error(
                "Blend POS finalization failed: order_id=%s reference=%s failed=%s "
                "delivery_total=%s delivery_tax=%s pos_state=%s pos_total=%s "
                "pos_tax=%s pos_paid=%s invoice_state=%s invoice_total=%s "
                "invoice_tax=%s invoice_residual=%s",
                self.id, self.external_order_id, ",".join(failed),
                self.amount_total, self.vat_amount,
                pos_order.state, pos_order.amount_total, pos_order.amount_tax,
                pos_order.amount_paid, invoice.state if invoice else False,
                invoice.amount_total if invoice else False,
                invoice.amount_tax if invoice else False,
                invoice.amount_residual if invoice else False,
            )
            raise

    def _blend_promotion_product(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", (194020, self.company_id.id),
        )
        Product = self.env["product.product"].sudo().with_company(self.company_id)
        product = Product.with_context(active_test=False).search([
            ("default_code", "=", "BLEND-DISCOUNT"),
            ("company_id", "=", self.company_id.id),
        ], limit=1)
        if not product:
            product = Product.create({
                "name": _("Blend Promotion Discount"),
                "default_code": "BLEND-DISCOUNT",
                "company_id": self.company_id.id,
                "type": "service",
                "sale_ok": False,
                "purchase_ok": False,
                "available_in_pos": False,
                "taxes_id": [Command.clear()],
                "list_price": 0.0,
                "responsible_id": False,
            })
        if (not product.active or product.type != "service"
                or product.sale_ok or product.available_in_pos):
            raise UserError(_("Blend promotion product must be an active hidden service."))
        return product

    def _blend_original_gross_by_line(self):
        """Use the validated incoming item totals when Blend states a header promotion."""
        self.ensure_one()
        raw = self.webhook_event_id.raw_payload
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
            items = payload["products"]
            gross_by_line = {}
            for index, item in enumerate(items, 1):
                qty = Decimal(str(item["quantity"]))
                gross_by_line["item-%s" % index] = Decimal(str(item["total"]))
                for option_index, option in enumerate(item.get("modifiers", []), 1):
                    gross_by_line["item-%s-option-%s" % (index, option_index)] = (
                        Decimal(str(option["total"])) * qty
                    )
            if sum(gross_by_line.values(), Decimal(0)) > Decimal(str(payload["total"])):
                return gross_by_line
        except (KeyError, TypeError, ValueError, InvalidOperation):
            pass
        return {}

    def _append_operational_amount_lines(self, commands, current_total, store):
        self.ensure_one()
        item_lines = self._pos_order_item_lines()
        result = super()._append_operational_amount_lines(commands, current_total, store)
        if (self.provider_id.code != "blend" or self.contract_version != 1
                or not any(line.discount_amount for line in item_lines)):
            return result

        tax_model = self.env["account.tax"].with_company(self.company_id)
        money_engine = self.env["bs.delivery.money.engine"]
        original_gross = self._blend_original_gross_by_line()
        discounts_by_taxes = {}
        for line, command in zip(item_lines, commands, strict=False):
            if not line.discount_amount:
                continue
            values = command[2]
            taxes = tax_model.browse(values["tax_ids"][0][2])
            effective_taxes = store.fiscal_position_id.map_tax(taxes)
            if line.external_line_id in original_gross:
                values["price_unit"] = money_engine._price_unit_from_gross(
                    self, line.product_id, effective_taxes,
                    float(original_gross[line.external_line_id]), line.quantity,
                )
            base_line = tax_model._prepare_base_line_for_taxes_computation(
                None, currency_id=self.currency_id, product_id=line.product_id,
                tax_ids=effective_taxes, price_unit=values["price_unit"],
                quantity=values["qty"], discount=0.0,
            )
            tax_model._add_tax_details_in_base_lines([base_line], self.company_id)
            tax_model._round_base_lines_tax_details([base_line], self.company_id)
            full_gross = base_line["tax_details"]["total_included_currency"]
            discounted_gross = line.price_subtotal + line.vat_amount
            discount_gross = self.currency_id.round(full_gross - discounted_gross)
            if discount_gross <= 0:
                raise UserError(_("Blend promotion discount must reduce the item total."))
            tax_key = tuple(sorted(taxes.ids))
            if tax_key not in discounts_by_taxes:
                discounts_by_taxes[tax_key] = 0.0
            discounts_by_taxes[tax_key] += discount_gross
            values["discount"] = 0.0

        product = self._blend_promotion_product()
        for tax_key, gross in discounts_by_taxes.items():
            taxes = tax_model.browse(tax_key)
            effective_taxes = store.fiscal_position_id.map_tax(taxes)
            amount = money_engine._price_unit_from_gross(
                self, product, effective_taxes, -self.currency_id.round(gross),
            )
            commands.append(self._pos_adjustment_command(
                product, amount, _("Blend Promotion Discount"), taxes,
            ))
        return result


    def _resume_awaiting_pos_session(self, *, retry_blocked=False):
        for order in self:
            if order.provider_id.code == "blend":
                try:
                    order = order.with_env(order.connection_id._blend_execution_environment()).sudo()
                except DeliveryAuthenticationError as error:
                    if retry_blocked:
                        raise
                    if order.pos_processing_state in ("waiting_session", "blocked"):
                        order.sudo()._set_pos_processing_result("blocked", str(error))
                    continue
            super(DeliveryOrder, order)._resume_awaiting_pos_session(retry_blocked=retry_blocked)
        return True

    def _pos_order_preset(self, store):
        preset = super()._pos_order_preset(store)
        if self.provider_id.code != "blend":
            return preset
        field_name = {
            "delivery": "blend_delivery_preset_id",
            "pickup": "blend_pickup_preset_id",
            "dine_in": "blend_dine_in_preset_id",
        }.get(self.fulfillment_type)
        selected = store[field_name] if field_name else False
        if selected:
            store._validate_blend_preset(selected)
            return selected
        return preset

    def _create_pos_order_hook(self):
        pos_order = super()._create_pos_order_hook()
        if self.provider_id.code == "blend":
            api_order_id = self.blend_api_order_id or str(uuid.uuid4())
            if not self.blend_api_order_id:
                self.sudo()._with_delivery_internal_update().write({
                    "blend_api_order_id": api_order_id,
                })
            vals = {
                "blend_order_id": api_order_id,
                "blend_status": "pending",
                "blend_partner_reference": self.external_order_id,
                "blend_aggregator": self.external_source,
                "blend_aggregator_order_id": self.external_reference or self.external_order_id,
                "blend_order_type": self.fulfillment_type,
            }
            if self.scheduled_at:
                vals["blend_schedule_at"] = str(self.scheduled_at)

            raw = self.webhook_event_id.raw_payload if self.webhook_event_id else None
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = None
            if isinstance(raw, dict):
                vals["blend_payload"] = raw
                customer = raw.get("customer") or {}
                car = (
                    customer.get("car")
                    or customer.get("car_data")
                    or raw.get("car")
                    or raw.get("car_data")
                    or {}
                )
                if isinstance(car, dict):
                    vals["blend_car_color"] = car.get("color") or ""
                    vals["blend_car_plate_number"] = car.get("plate_number") or ""
                    vals["blend_car_plate_letters"] = car.get("plate_letters") or ""
                    vals["blend_car_brand"] = car.get("car_brand") or car.get("brand") or ""

            pos_order.with_context(bs_blend_bridge_sync=True).write(vals)
        return pos_order

    def _after_delivery_state_transition(self, transition):
        result = super()._after_delivery_state_transition(transition)
        if self.provider_id.code == "blend" and self.pos_order_id:
            blend_status = {
                "received": "pending", "accepted": "accepted",
                "preparing": "preparing", "ready": "prepared",
                "picked_up": "at_pick_up", "delivered": "delivered",
                "rejected": "rejected", "cancelled": "cancelled",
            }.get(self.state)
            if blend_status and self.pos_order_id.blend_status != blend_status:
                self.pos_order_id.with_context(bs_blend_bridge_sync=True).write({
                    "blend_status": blend_status,
                })
        return result
