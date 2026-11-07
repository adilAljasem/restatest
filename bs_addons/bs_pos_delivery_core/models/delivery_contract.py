"""Validation boundary for normalized v1 data; never performs business writes."""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext

from odoo import api, models

from ..exceptions import DeliveryBusinessError
from .delivery_order import DELIVERY_ORDER_STATES


class DeliveryContract(models.AbstractModel):
    _name = "bs.delivery.contract"
    _description = "Delivery Core Payload Contract"

    @api.model
    def _validate_order_payload(self, payload, *, currency):
        """Return an independent validated snapshot in currency major units.

        Currency is a trusted Core record, not an ID from the payload. This
        validates structure and arithmetic only; the intake service must still
        validate event/company/store scope, mappings and Odoo tax calculations.
        """
        required = {
            "contract_version", "external_order_id", "external_source",
            "currency", "fulfillment_type", "price_basis", "lines", "totals",
        }
        data = self._object(payload, required, {
            "external_reference", "external_status", "scheduled_at", "customer",
            "fees", "payments", "untaxed_discounts",
        }, "order")
        self._version(data)
        for key in ("external_order_id", "external_source"):
            data[key] = self._text(data[key], key)
        self._currency(data, currency)
        self._choice(data["fulfillment_type"], {"delivery", "pickup", "dine_in"}, "fulfillment_type")
        self._choice(data["price_basis"], {"tax_excluded"}, "price_basis")
        for key in ("external_reference", "external_status"):
            if key in data:
                data[key] = self._text(data[key], key)
        if "scheduled_at" in data:
            data["scheduled_at"] = self._timestamp(data["scheduled_at"], "scheduled_at")
        customer = self._object(data.get("customer", {}), set(), {
            "name", "phone", "email", "address", "note",
        }, "customer")
        data["customer"] = {
            key: self._text(value, "customer." + key, allow_empty=True)
            for key, value in customer.items()
        }

        lines = self._list(data["lines"], "lines")
        if not lines:
            self._invalid("lines", "at least one item is required")
        line_fields = {
            "external_line_id", "external_product_id", "name", "quantity",
            "price_unit", "discount_amount", "vat_amount", "price_subtotal",
        }
        by_id = {}
        gross = discount = subtotal = tax = Decimal(0)
        for index, incoming in enumerate(lines):
            path = "lines[%s]" % index
            line = self._object(incoming, line_fields, {"parent_external_line_id", "note"}, path)
            for key in ("external_line_id", "external_product_id", "name"):
                line[key] = self._text(line[key], path + "." + key)
            identity = line["external_line_id"]
            if identity in by_id:
                self._invalid(path, "external_line_id must be unique within the order")
            quantity = self._number(line["quantity"], path + ".quantity", positive=True)
            price = self._number(line["price_unit"], path + ".price_unit")
            line_gross = self._round(quantity * price, currency)
            line_discount = self._money(line["discount_amount"], currency, path + ".discount_amount")
            line_subtotal = self._money(line["price_subtotal"], currency, path + ".price_subtotal")
            line_tax = self._money(line["vat_amount"], currency, path + ".vat_amount")
            if line_discount > line_gross or line_subtotal != line_gross - line_discount:
                self._invalid(path, "subtotal must equal rounded quantity times price minus discount")
            if "parent_external_line_id" in line:
                line["parent_external_line_id"] = self._text(
                    line["parent_external_line_id"], path + ".parent_external_line_id"
                )
            if "note" in line:
                line["note"] = self._text(line["note"], path + ".note", allow_empty=True)
            lines[index] = line
            by_id[identity] = line
            gross += line_gross
            discount += line_discount
            subtotal += line_subtotal
            tax += line_tax
        for line in lines:
            parent_id = line.get("parent_external_line_id")
            if parent_id is not None:
                parent = by_id.get(parent_id)
                if not parent or parent_id == line["external_line_id"] or "parent_external_line_id" in parent:
                    self._invalid("parent_external_line_id", "must reference a base item in this order")
        data["lines"] = lines

        fees = self._list(data.get("fees", []), "fees")
        fee_total = Decimal(0)
        kinds = set()
        for index, incoming in enumerate(fees):
            fee = self._object(incoming, {"kind", "amount", "vat_amount"}, set(), "fees")
            self._choice(fee["kind"], {"delivery", "service", "small_order", "tip"}, "fees.kind")
            if fee["kind"] in kinds:
                self._invalid("fees.kind", "each fee kind may appear only once")
            kinds.add(fee["kind"])
            fee_total += self._money(fee["amount"], currency, "fees.amount")
            tax += self._money(fee["vat_amount"], currency, "fees.vat_amount")
            fees[index] = fee
        data["fees"] = fees

        untaxed_discounts = self._list(data.get("untaxed_discounts", []), "untaxed_discounts")
        untaxed_discount_total = Decimal(0)
        for index, incoming in enumerate(untaxed_discounts):
            path = "untaxed_discounts[%s]" % index
            component = self._object(incoming, {"label", "amount"}, set(), path)
            component["label"] = self._text(component["label"], path + ".label")
            if len(component["label"]) > 128:
                self._invalid(path + ".label", "must be at most 128 characters")
            amount = self._money(component["amount"], currency, path + ".amount")
            if amount <= 0:
                self._invalid(path + ".amount", "must be positive")
            untaxed_discount_total += amount
            untaxed_discounts[index] = component
        if "untaxed_discounts" in data:
            data["untaxed_discounts"] = untaxed_discounts

        totals = self._object(data["totals"], {
            "items_subtotal", "discount_amount", "fee_amount", "vat_amount",
            "rounding_amount", "amount_total",
        }, set(), "totals")
        amounts = {
            key: self._money(value, currency, "totals." + key, signed=key == "rounding_amount")
            for key, value in totals.items()
        }
        # Arbitrary balancing amounts would conceal price or tax mismatches.
        if amounts["rounding_amount"] != 0:
            self._invalid("totals.rounding_amount", "v1 requires zero; cash rounding needs a separate policy")
        expected = {
            "items_subtotal": gross, "discount_amount": discount + untaxed_discount_total,
            "fee_amount": fee_total, "vat_amount": tax,
            "amount_total": subtotal + fee_total + tax - untaxed_discount_total,
        }
        for key, value in expected.items():
            if amounts[key] != value:
                self._invalid("totals." + key, "does not match the line and fee amounts")
        data["totals"] = totals

        payments = self._list(data.get("payments", []), "payments")
        references = set()
        paid_or_expected = Decimal(0)
        for index, incoming in enumerate(payments):
            payment = self._validate_payment_payload(incoming, currency=currency)
            reference = payment.get("external_payment_reference")
            if reference is not None:
                if reference in references:
                    self._invalid("payments", "payment references must be unique within the order")
                references.add(reference)
            paid_or_expected += Decimal(str(payment["amount"]))
            payments[index] = payment
        if paid_or_expected > amounts["amount_total"]:
            self._invalid("payments", "payment allocations exceed the order total")
        data["payments"] = payments
        return data

    @api.model
    def _validate_payment_payload(self, payload, *, currency):
        """Validate a payment allocation; this neither resolves nor posts it."""
        data = self._object(payload, {
            "external_payment_code", "external_source", "amount", "currency",
            "collected_by", "collection_status",
        }, {"external_payment_reference", "external_name"}, "payment")
        self._currency(data, currency)
        for key in ("external_payment_code", "external_source", "external_payment_reference", "external_name"):
            if key in data:
                data[key] = self._text(data[key], "payment." + key)
        amount = self._money(data["amount"], currency, "payment.amount")
        if amount <= 0:
            self._invalid("payment.amount", "must be positive")
        self._choice(data["collected_by"], {"restaurant", "aggregator", "unknown"}, "payment.collected_by")
        self._choice(data["collection_status"], {"expected", "collected"}, "payment.collection_status")
        if data["collection_status"] == "collected":
            if data["collected_by"] == "unknown" or not data.get("external_payment_reference"):
                self._invalid("payment", "collected payments require a known collector and external reference")
        return data

    @api.model
    def _validate_provider_update(self, payload):
        """Validate status metadata, without authorizing or applying a transition."""
        data = self._object(payload, {"contract_version", "state", "occurred_at"}, {"external_status"}, "update")
        self._version(data)
        self._choice(data["state"], {key for key, _label in DELIVERY_ORDER_STATES}, "state")
        data["occurred_at"] = self._timestamp(data["occurred_at"], "occurred_at")
        if "external_status" in data:
            data["external_status"] = self._text(data["external_status"], "external_status")
        return data

    @staticmethod
    def _invalid(path, reason):
        # Do not echo customer data or arbitrary payload values in operator logs.
        raise DeliveryBusinessError(
            "Invalid Core v1 payload at %s: %s." % (path, reason),
            code="validation_error",
        )

    def _object(self, value, required, optional, path):
        if not isinstance(value, dict):
            self._invalid(path, "must be an object")
        if not required.issubset(value) or set(value) - required - optional:
            self._invalid(path, "missing required fields or contains unsupported fields")
        return deepcopy(value)

    def _list(self, value, path):
        if not isinstance(value, list):
            self._invalid(path, "must be a list")
        return deepcopy(value)

    def _text(self, value, path, *, allow_empty=False):
        if not isinstance(value, str) or (not allow_empty and not value.strip()):
            self._invalid(path, "must be a nonempty string" if not allow_empty else "must be a string")
        return value.strip()

    def _choice(self, value, choices, path):
        if not isinstance(value, str) or value not in choices:
            self._invalid(path, "unsupported value")

    def _version(self, data):
        if type(data["contract_version"]) is not int or data["contract_version"] != 1:
            self._invalid("contract_version", "must be integer 1")

    def _currency(self, data, currency):
        currency.ensure_one()
        if data["currency"] != currency.name:
            self._invalid("currency", "must match the currency selected by Core")

    def _number(self, value, path, *, positive=False, signed=False):
        if type(value) not in (int, float):
            self._invalid(path, "must be a finite JSON number")
        number = Decimal(str(value))
        if not number.is_finite() or (not signed and number < 0) or (positive and number <= 0):
            self._invalid(path, "invalid amount or quantity")
        # Keep arithmetic within Decimal precision and the existing float fields.
        if abs(number) > Decimal("1e12") or number.as_tuple().exponent < -12:
            self._invalid(path, "maximum magnitude is 1e12 with at most 12 decimal places")
        return number

    def _round(self, amount, currency):
        increment = Decimal(str(currency.rounding))
        with localcontext() as context:
            context.prec = 64
            return (amount / increment).quantize(Decimal(1), rounding=ROUND_HALF_UP) * increment

    def _money(self, value, currency, path, *, signed=False):
        amount = self._number(value, path, signed=signed)
        if self._round(amount, currency) != amount:
            self._invalid(path, "must respect the currency rounding increment")
        return amount

    def _timestamp(self, value, path):
        if not isinstance(value, str):
            self._invalid(path, "must be an ISO 8601 timestamp with timezone")
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if timestamp.utcoffset() is None:
                raise ValueError("timezone required")
            return timestamp.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            self._invalid(path, "must be an ISO 8601 timestamp with timezone")
