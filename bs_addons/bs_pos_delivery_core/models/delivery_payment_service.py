"""Core v1 payment resolution and reference-safe POS payment recording."""

from odoo import _, api, fields, models

from ..exceptions import DeliveryBusinessError
from .delivery_pos_payment_mapping import aggregator_key


class DeliveryPaymentService(models.AbstractModel):
    _name = "bs.delivery.payment.service"
    _description = "Delivery Core Payment Service"

    @api.model
    def _resolve_payment_method(
        self, connection, store, *, external_code, collected_by,
        external_source="default",
    ):
        """Resolve a platform name and normalized payment type on the connection and branch."""
        if (getattr(connection, "_name", None) != "bs.delivery.connection" or len(connection) != 1
                or not connection.exists() or not connection.active):
            raise DeliveryBusinessError(_("Choose an active delivery connection."), code="invalid_connection")
        if (getattr(store, "_name", None) != "bs.delivery.store.binding" or len(store) != 1
                or not store.exists() or not store.active
                or store.connection_id != connection
                or store.company_id != connection.company_id
                or store.pos_config_id.company_id != connection.company_id
                or not store.pos_config_id.active):
            raise DeliveryBusinessError(_("Choose an active store for this connection."), code="invalid_store")
        if collected_by not in ("restaurant", "aggregator", "unknown"):
            raise DeliveryBusinessError(_("Invalid payment collector."), code="unknown_collector")
        return self._resolve_connection_mapping(
            connection, store.pos_config_id, aggregator_name=external_source,
            payment_type=external_code, company=connection.company_id,
        ).payment_method_id

    @api.model
    def _resolve_connection_mapping(
        self, connection, pos_config, *, aggregator_name, payment_type, company,
    ):
        """Resolve one explicit connection and branch payment row."""
        if (getattr(connection, "_name", None) != "bs.delivery.connection" or len(connection) != 1
                or not connection.exists() or not connection.active or connection.company_id != company):
            raise DeliveryBusinessError(_("Choose an active delivery connection."), code="invalid_connection")
        if (getattr(pos_config, "_name", None) != "pos.config" or len(pos_config) != 1
                or not pos_config.exists() or not pos_config.active
                or pos_config.company_id != company):
            raise DeliveryBusinessError(_("Choose an active POS branch in the payment company."), code="invalid_store")
        if isinstance(payment_type, bool) or not isinstance(payment_type, (str, int)):
            raise DeliveryBusinessError(_("A platform payment code is required."), code="invalid_payment_code")
        code = str(payment_type).strip()
        if not code:
            raise DeliveryBusinessError(_("A platform payment code is required."), code="invalid_payment_code")
        if not isinstance(aggregator_name, str) or not aggregator_name.strip():
            raise DeliveryBusinessError(_("A platform payment source is required."), code="invalid_payment_source")
        normalized_type = {
            "cash": "cash", "cod": "cash", "cash on delivery": "cash", "1": "cash",
            "visa": "credit_visa", "card": "credit_visa", "credit": "credit_visa",
            "credit/visa": "credit_visa", "credit_visa": "credit_visa", "0": "credit_visa",
            "wallet": "wallet",
        }.get(code.casefold())
        if not normalized_type:
            raise DeliveryBusinessError(_("Unsupported platform payment type."), code="invalid_payment_code")
        mappings = self.env["bs.delivery.pos.payment.mapping"].sudo().with_company(company).search([
            ("connection_id", "=", connection.id),
            ("pos_config_id", "=", pos_config.id),
            ("aggregator_key", "=", aggregator_key(aggregator_name)),
            ("payment_type", "=", normalized_type),
        ])
        if not mappings:
            raise DeliveryBusinessError(_("Configure this aggregator and payment type on the POS branch."), code="payment_mapping_not_found")
        if len(mappings) != 1:
            raise DeliveryBusinessError(_("More than one POS payment mapping matches."), code="ambiguous_payment_mapping")
        method = mappings.payment_method_id
        if (not method.active or method.company_id != company
                or method not in pos_config.payment_method_ids):
            raise DeliveryBusinessError(_("The mapped payment method is unavailable in this store's POS."), code="invalid_payment_mapping")
        return mappings

    @api.model
    def _record_payment(self, order, payload, *, event, post_expected_cash=False, allocation=None):
        """Post a POS payment once; acceptance may post expected cash."""
        if (getattr(order, "_name", None) != "bs.delivery.order"
                or len(order) != 1 or not order.exists() or order.contract_version != 1):
            raise DeliveryBusinessError(_("A Core v1 delivery order is required."), code="invalid_order")
        data = self.env["bs.delivery.contract"]._validate_payment_payload(
            payload, currency=order.currency_id,
        )
        if data["collection_status"] != "collected" and not (
            post_expected_cash and data["collection_status"] == "expected"
            and data["external_payment_code"].strip().casefold() in ("cash", "cod", "1")
        ):
            raise DeliveryBusinessError(_("Expected payments cannot be posted as collected money."), code="payment_not_collected")
        self.env["bs.delivery.order.service"]._validate_event_scope(
            order.connection_id, order.store_binding_id, event, order.external_order_id,
        )
        if allocation and (
            getattr(allocation, "_name", None) != "bs.delivery.payment.allocation"
            or len(allocation) != 1 or not allocation.exists() or allocation.order_id != order
        ):
            raise DeliveryBusinessError(_("The payment allocation belongs to another order."), code="invalid_payment_allocation")
        Allocation = self.env["bs.delivery.payment.allocation"].sudo().with_company(order.company_id)
        if allocation:
            allocation = allocation.sudo().with_company(order.company_id)
        with self.env.cr.savepoint():
            self.env.cr.execute("SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE", [order.id])
            order.invalidate_recordset()
            reference = data.get("external_payment_reference")
            if not allocation:
                allocation = Allocation.search([
                    ("order_id", "=", order.id),
                    ("external_payment_reference", "=", reference),
                ], limit=1) if reference else Allocation.search([
                    ("order_id", "=", order.id),
                    ("external_source", "=", data["external_source"]),
                    ("external_payment_code", "=", data["external_payment_code"]),
                    ("collection_status", "=", "expected"),
                    ("amount", "=", data["amount"]),
                    ("external_payment_reference", "=", False),
                ], limit=2)
                if len(allocation) > 1:
                    raise DeliveryBusinessError(_("Ambiguous expected payment; a reference is required."), code="payment_reference_conflict")
            if not allocation and reference:
                candidates = Allocation.search([
                    ("order_id", "=", order.id),
                    ("external_source", "=", data["external_source"]),
                    ("external_payment_code", "=", data["external_payment_code"]),
                    ("collection_status", "=", "expected"),
                    ("collected_by", "in", ["unknown", data["collected_by"]]),
                    ("amount", "=", data["amount"]),
                    ("external_payment_reference", "=", False),
                ], limit=2)
                if len(candidates) > 1:
                    raise DeliveryBusinessError(_("Ambiguous expected payment; a reference is required."), code="payment_reference_conflict")
                allocation = candidates
            if allocation:
                if any((
                    allocation.external_source != data["external_source"],
                    allocation.external_payment_code != data["external_payment_code"],
                    bool(allocation.external_payment_reference and allocation.external_payment_reference != reference),
                    allocation.collected_by not in ("unknown", data["collected_by"]),
                    allocation.collection_status not in ("expected", "collected"),
                    allocation.collection_status == "collected" and allocation.collected_by != data["collected_by"],
                    order.currency_id.compare_amounts(allocation.amount, data["amount"]) != 0,
                )):
                    raise DeliveryBusinessError(_("The payment reference was reused with different details."), code="payment_reference_conflict")
            if allocation and allocation.pos_payment_id:
                payment = allocation.pos_payment_id
                if (not payment.exists() or payment.pos_order_id != order.pos_order_id
                        or payment.payment_method_id != allocation.payment_method_id
                        or order.currency_id.compare_amounts(payment.amount, data["amount"])):
                    raise DeliveryBusinessError(_("The recorded POS payment no longer matches its delivery reference."), code="payment_reference_conflict")
                if data["collection_status"] == "collected" and allocation.collection_status == "expected":
                    allocation.write({
                        "collection_status": "collected",
                        "collected_by": data["collected_by"],
                        "external_payment_reference": reference,
                        "webhook_event_id": event.id,
                    })
                    self._update_prepaid_from_collections(order)
                return payment
            method = self._resolve_payment_method(
                order.connection_id, order.store_binding_id,
                external_code=data["external_payment_code"],
                external_source=data["external_source"],
                collected_by=data["collected_by"],
            )
            if order.state in ("delivered", "rejected", "cancelled"):
                raise DeliveryBusinessError(_("A terminal delivery order cannot accept a new payment."), code="payment_state_conflict")
            pos_order = order.pos_order_id
            if not pos_order or pos_order.state != "draft":
                raise DeliveryBusinessError(_("Create an open draft POS order before recording payment."), code="pos_unavailable")
            if pos_order.session_id.state not in ("opening_control", "opened"):
                raise DeliveryBusinessError(_("The POS session is not open for a new payment."), code="pos_unavailable")
            if order._compare_provider_financial_amounts(order.amount_total, pos_order.amount_total):
                raise DeliveryBusinessError(_("The POS total differs from the delivery order."), code="payment_total_conflict")
            self.env.cr.execute("SELECT id FROM pos_order WHERE id = %s FOR UPDATE", [pos_order.id])
            pos_order.invalidate_recordset(["amount_paid", "payment_ids"])
            if order.currency_id.compare_amounts(
                pos_order.amount_paid + data["amount"], order.amount_total,
            ) > 0:
                raise DeliveryBusinessError(_("The payment exceeds the remaining POS balance."), code="payment_overallocation")
            if not allocation:
                expected = Allocation.search([
                    ("order_id", "=", order.id),
                    ("external_source", "=", data["external_source"]),
                    ("external_payment_code", "=", data["external_payment_code"]),
                    ("collection_status", "=", "expected"),
                    ("collected_by", "in", ["unknown", data["collected_by"]]),
                    ("external_payment_reference", "in", [False, reference]),
                    ("amount", "=", data["amount"]),
                ], order="id", limit=1)
                if expected:
                    allocation = expected
                else:
                    allocated = sum(order.payment_allocation_ids.mapped("amount"))
                    if order.currency_id.compare_amounts(allocated + data["amount"], order.amount_total) > 0:
                        raise DeliveryBusinessError(_("Payment allocations exceed the delivery total."), code="payment_overallocation")
                    allocation = Allocation.create({
                        "order_id": order.id,
                        "external_source": data["external_source"],
                        "external_payment_code": data["external_payment_code"],
                        "external_name": data.get("external_name"),
                        "amount": data["amount"],
                        "collected_by": data["collected_by"],
                        "collection_status": data["collection_status"],
                        "external_payment_reference": reference,
                        "webhook_event_id": event.id,
                    })
            if allocation.payment_method_id and allocation.payment_method_id != method:
                raise DeliveryBusinessError(_("The payment reference resolved to another POS method."), code="payment_reference_conflict")
            payment = self.env["pos.payment"].sudo().with_company(order.company_id).create({
                "pos_order_id": pos_order.id,
                "payment_method_id": method.id,
                "amount": data["amount"],
                "payment_date": fields.Datetime.now(),
            })
            pos_order.amount_paid = pos_order._compute_amount_paid()
            allocation.write({
                "collection_status": data["collection_status"],
                "collected_by": data["collected_by"],
                "external_payment_reference": reference,
                "external_name": data.get("external_name"),
                "payment_method_id": method.id,
                "pos_payment_id": payment.id,
                "webhook_event_id": event.id,
            })
            self._update_prepaid_from_collections(order)
            order._notify_pos_board("payment_changed")
            return payment

    @api.model
    def _update_prepaid_from_collections(self, order):
        collected = sum(order.payment_allocation_ids.filtered(
            lambda row: row.collection_status == "collected" and row.pos_payment_id,
        ).mapped("amount"))
        order._with_delivery_internal_update().write({
            "is_prepaid": bool(order.amount_total and not order.currency_id.compare_amounts(
                collected, order.amount_total,
            )),
        })
