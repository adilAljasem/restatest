"""Authenticated Core v1 order intake and provider state updates."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal

from odoo import Command, _, api, models
from odoo.exceptions import UserError

from ..exceptions import DeliveryBusinessError


class DeliveryOrderService(models.AbstractModel):
    _name = "bs.delivery.order.service"
    _description = "Delivery Core Order Service"

    @api.model
    def _connection_allows_provider_event(self, connection):
        return connection.active and connection.state == "connected"

    @api.model
    def _validate_event_scope(self, connection, store, event, external_order_id=None):
        """Check trusted routing records before elevated order or payment work."""
        if (getattr(connection, "_name", None) != "bs.delivery.connection"
                or len(connection) != 1 or not connection.exists()
                or not self._connection_allows_provider_event(connection)):
            raise DeliveryBusinessError(_("An active connected delivery connection is required."), code="invalid_connection")
        if (getattr(store, "_name", None) != "bs.delivery.store.binding"
                or len(store) != 1 or not store.exists() or not store.active
                or store.connection_id != connection
                or store.company_id != connection.company_id
                or store.pos_config_id.company_id != connection.company_id):
            raise DeliveryBusinessError(_("The delivery store does not belong to this connection and company."), code="invalid_store")
        if (getattr(event, "_name", None) != "bs.delivery.webhook.event"
                or len(event) != 1 or not event.exists()
                or not event.signature_verified
                or event.connection_id != connection
                or (event.store_binding_id and event.store_binding_id != store)):
            raise DeliveryBusinessError(_("An authenticated event for this connection and store is required."), code="invalid_event")
        if external_order_id and event.external_id and event.external_id != external_order_id:
            raise DeliveryBusinessError(_("The event refers to another external order."), code="invalid_event")
        if external_order_id and event.binding_id and (
            event.binding_id.connection_id != connection
            or event.binding_id.external_order_id != external_order_id
        ):
            raise DeliveryBusinessError(_("The event is linked to another order."), code="invalid_event")
        return True

    @staticmethod
    def _utc_naive(value):
        return datetime.fromisoformat(value).replace(tzinfo=None)

    @staticmethod
    def _canonical_snapshot(value):
        if type(value) in (int, float):
            return {"number": format(Decimal(str(value)).normalize(), "f")}
        if isinstance(value, list):
            return [DeliveryOrderService._canonical_snapshot(item) for item in value]
        if isinstance(value, dict):
            return {
                key: DeliveryOrderService._canonical_snapshot(item)
                for key, item in value.items()
            }
        return value

    @api.model
    def _receive_order(self, connection, store, payload, *, event):
        """Create one canonical order or return it on an exact authenticated replay."""
        self._validate_event_scope(connection, store, event)
        normalized = self.env["bs.delivery.contract"]._validate_order_payload(
            payload, currency=store.pos_config_id.currency_id,
        )
        external_id = normalized["external_order_id"]
        self._validate_event_scope(connection, store, event, external_id)
        snapshot = json.dumps(
            self._canonical_snapshot(normalized),
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        snapshot_hash = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
        Order = self.env["bs.delivery.order"].sudo().with_company(connection.company_id)
        Binding = self.env["bs.delivery.order.binding"].sudo().with_company(connection.company_id)
        with self.env.cr.savepoint():
            binding, _created = Binding.get_or_create(connection, external_id, {
                "external_reference": normalized.get("external_reference"),
                "external_source": normalized["external_source"],
                "external_status": normalized.get("external_status"),
            })
            self.env.cr.execute("SELECT id FROM bs_delivery_order_binding WHERE id = %s FOR UPDATE", [binding.id])
            order = Order.search([
                ("connection_id", "=", connection.id),
                ("external_order_id", "=", external_id),
            ], limit=1)
            if order:
                if order.store_binding_id != store or order.contract_version != 1:
                    raise DeliveryBusinessError(_("This external order belongs to another Core store or workflow."), code="order_identity_conflict")
                if order.contract_snapshot_hash != snapshot_hash:
                    raise DeliveryBusinessError(_("A replay changed the original order snapshot. Review a financial correction separately."), code="order_snapshot_conflict")
                if event.binding_id and event.binding_id != binding:
                    raise DeliveryBusinessError(_("The event is linked to another order."), code="invalid_event")
                if not event.binding_id:
                    event.sudo().write({"binding_id": binding.id})
                return order

            self.env["bs.delivery.webhook.event"]._assert_receiving_available(connection, store)
            if binding.pos_order_id:
                raise DeliveryBusinessError(_("This external order already belongs to a legacy POS order."), code="order_identity_conflict")

            totals = normalized["totals"]
            fees = {fee["kind"]: fee["amount"] + fee["vat_amount"] for fee in normalized["fees"]}
            customer = normalized["customer"]
            total = totals["amount_total"]
            rate = connection.commission_pct
            commission = store.pos_config_id.currency_id.round(total * rate / 100)
            collected = sum(
                payment["amount"] for payment in normalized["payments"]
                if payment["collection_status"] == "collected"
            )
            values = {
                "connection_id": connection.id,
                "store_binding_id": store.id,
                "binding_id": binding.id,
                "webhook_event_id": event.id,
                "contract_version": 1,
                "contract_snapshot_hash": snapshot_hash,
                "external_order_id": external_id,
                "external_source": normalized["external_source"],
                "external_reference": normalized.get("external_reference"),
                "external_status": normalized.get("external_status"),
                "fulfillment_type": normalized["fulfillment_type"],
                "currency_id": store.pos_config_id.currency_id.id,
                "scheduled_at": self._utc_naive(normalized["scheduled_at"]) if normalized.get("scheduled_at") else False,
                "customer_name": customer.get("name"),
                "customer_phone": customer.get("phone"),
                "customer_email": customer.get("email"),
                "delivery_address": customer.get("address"),
                "customer_note": customer.get("note"),
                "items_subtotal": totals["items_subtotal"],
                "discount_amount": totals["discount_amount"],
                "untaxed_discounts": normalized.get("untaxed_discounts", []),
                "delivery_fee": fees.get("delivery", 0),
                "service_fee": fees.get("service", 0),
                "small_order_fee": fees.get("small_order", 0),
                "tip_amount": fees.get("tip", 0),
                "vat_amount": totals["vat_amount"],
                "amount_total": total,
                "commission_rate": rate,
                "commission_amount": commission,
                "net_amount": total - commission,
                "is_prepaid": bool(total and store.pos_config_id.currency_id.compare_amounts(collected, total) == 0),
                "remote_payment_method": normalized["payments"][0]["external_payment_code"] if len(normalized["payments"]) == 1 else False,
                "payment_allocation_ids": [Command.create({
                    "external_source": payment["external_source"],
                    "external_payment_code": payment["external_payment_code"],
                    "external_name": payment.get("external_name"),
                    "amount": payment["amount"],
                    "collected_by": payment["collected_by"],
                    "collection_status": payment["collection_status"],
                    "external_payment_reference": payment.get("external_payment_reference"),
                    "webhook_event_id": event.id,
                }) for payment in normalized["payments"]],
            }
            order = Order._with_delivery_internal_update().with_context(
                bs_delivery_defer_initial_acceptance=True,
            ).create(values)
            indexed_lines = list(enumerate(normalized["lines"], 1))
            for sequence, line in indexed_lines:
                if line.get("parent_external_line_id"):
                    continue
                line_values = self._line_values(connection, store, line, sequence)
                order._with_delivery_internal_update().write({
                    "line_ids": [Command.create(line_values)],
                })
            for sequence, line in indexed_lines:
                if not line.get("parent_external_line_id"):
                    continue
                parent = order.line_ids.filtered(
                    lambda row: row.external_line_id == line["parent_external_line_id"]
                )
                line_values = self._line_values(connection, store, line, sequence)
                line_values.update({"is_modifier": True, "parent_line_id": parent.id})
                order._with_delivery_internal_update().write({
                    "line_ids": [Command.create(line_values)],
                })
            event.sudo().write({"binding_id": binding.id})
            try:
                with self.env.cr.savepoint():
                    order._apply_initial_acceptance_policy()
            except UserError as error:
                order._set_pos_processing_result("blocked", str(error))
            return order

    @api.model
    def _line_values(self, connection, store, line, sequence):
        product = self.env["bs.delivery.product.binding"].sudo().resolve_order_product(
            connection, store, line["external_product_id"],
            modifier=bool(line.get("parent_external_line_id")),
        )
        return {
            "sequence": sequence * 10,
            "external_line_id": line["external_line_id"],
            "external_product_id": line["external_product_id"],
            "external_sku": line["external_product_id"],
            "name": line["name"],
            "product_id": product.id or False,
            "quantity": line["quantity"],
            "price_unit": line["price_unit"],
            "discount_amount": line["discount_amount"],
            "vat_amount": line["vat_amount"],
            "price_subtotal": line["price_subtotal"],
            "customer_note": line.get("note"),
        }

    @api.model
    def _apply_provider_update(
        self, connection, store, external_order_id, payload, *, event
    ):
        """Advance an existing order without echoing provider states to the outbox."""
        external_order_id = str(external_order_id or "").strip()
        self._validate_event_scope(connection, store, event, external_order_id)
        update = self.env["bs.delivery.contract"]._validate_provider_update(payload)
        Order = self.env["bs.delivery.order"].sudo().with_company(connection.company_id)
        with self.env.cr.savepoint():
            order = Order.search([
                ("connection_id", "=", connection.id),
                ("external_order_id", "=", external_order_id),
            ], limit=1)
            if not order:
                raise DeliveryBusinessError(_("The provider update refers to an order Core has not received."), code="order_not_found", retryable=True)
            if order.store_binding_id != store or order.contract_version != 1:
                raise DeliveryBusinessError(_("The provider update belongs to another store or workflow."), code="order_identity_conflict")
            self.env.cr.execute("SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE", [order.id])
            order.invalidate_recordset()
            occurred = self._utc_naive(update["occurred_at"])
            if order.core_last_provider_update_at and occurred <= order.core_last_provider_update_at:
                return order
            target = update["state"]
            if order.state in ("delivered", "rejected", "cancelled") and target != order.state:
                raise DeliveryBusinessError(_("A terminal delivery order cannot change state."), code="state_conflict")
            if event.binding_id and event.binding_id != order.binding_id:
                raise DeliveryBusinessError(_("The event is linked to another order."), code="invalid_event")
            event.sudo().write({"binding_id": order.binding_id.id})
            progression = ("received", "accepted", "preparing", "ready", "picked_up", "delivered")
            if target == order.state:
                if update.get("external_status"):
                    order.transition_to(
                        target, source="webhook",
                        idempotency_key="core-update:%s:%s" % (event.id, target),
                        external_status=update["external_status"],
                    )
            elif target in ("rejected", "cancelled"):
                if target != order.state:
                    if target not in order._ALLOWED_TRANSITIONS[order.state]:
                        raise DeliveryBusinessError(_("The provider requested an invalid terminal transition."), code="state_conflict")
                    order.transition_to(
                        target, source="webhook", expected_state=order.state,
                        idempotency_key="core-update:%s:%s" % (event.id, target),
                        reason=_("Provider status update"),
                        external_status=update.get("external_status"),
                    )
            elif target in progression and order.state in progression:
                current = progression.index(order.state)
                desired = progression.index(target)
                if desired < current:
                    return order
                for state in progression[current + 1:desired + 1]:
                    order.transition_to(
                        state, source="webhook", expected_state=order.state,
                        idempotency_key="core-update:%s:%s" % (event.id, state),
                        external_status=update.get("external_status"),
                    )
            else:
                raise DeliveryBusinessError(_("The provider requested an invalid state transition."), code="state_conflict")
            if update.get("external_status") and update["external_status"] != order.external_status:
                order._with_delivery_internal_update().write({"external_status": update["external_status"]})
                order.binding_id.write({"external_status": update["external_status"]})
            order._with_delivery_internal_update().write({"core_last_provider_update_at": occurred})
            return order
