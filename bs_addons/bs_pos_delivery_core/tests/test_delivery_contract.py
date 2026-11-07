"""Executable payload contract tests and pending service acceptance scenarios."""

from copy import deepcopy

from odoo.tests import TransactionCase, tagged

from ..exceptions import DeliveryBusinessError


@tagged("post_install", "-at_install")
class TestDeliveryPayloadContract(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Contract = cls.env["bs.delivery.contract"]
        cls.currency = cls.env.ref("base.USD")

    def _payload(self):
        return {
            "contract_version": 1,
            "external_order_id": "ORDER-1",
            "external_source": "marketplace",
            "currency": "USD",
            "fulfillment_type": "delivery",
            "price_basis": "tax_excluded",
            "lines": [
                {
                    "external_line_id": "burger", "external_product_id": "SKU-1",
                    "name": "Burger", "quantity": 2, "price_unit": 20,
                    "discount_amount": 4, "vat_amount": 5.4, "price_subtotal": 36,
                },
                {
                    "external_line_id": "cheese", "external_product_id": "SKU-2",
                    "name": "Cheese", "quantity": 2, "price_unit": 1,
                    "discount_amount": 0, "vat_amount": 0.3, "price_subtotal": 2,
                    "parent_external_line_id": "burger",
                },
            ],
            "fees": [
                {"kind": "delivery", "amount": 3, "vat_amount": 0.45},
                {"kind": "tip", "amount": 2, "vat_amount": 0},
            ],
            "totals": {
                "items_subtotal": 42, "discount_amount": 4, "fee_amount": 5,
                "vat_amount": 6.15, "rounding_amount": 0, "amount_total": 49.15,
            },
            "payments": [{
                "external_payment_code": "visa", "external_source": "marketplace",
                "amount": 49.15, "currency": "USD", "collected_by": "aggregator",
                "collection_status": "collected", "external_payment_reference": "PAY-1",
            }],
        }

    def _validate(self, payload):
        return self.Contract._validate_order_payload(payload, currency=self.currency)

    def _assert_invalid(self, payload):
        with self.assertRaises(DeliveryBusinessError) as raised:
            self._validate(payload)
        self.assertEqual(raised.exception.code, "validation_error")
        self.assertFalse(raised.exception.retryable)

    def test_order_with_discount_charged_modifier_fees_and_payment(self):
        payload = self._payload()
        order_count = self.env["bs.delivery.order"].search_count([])
        payment_count = self.env["pos.payment"].search_count([])

        result = self._validate(payload)

        self.assertEqual(result["totals"]["amount_total"], 49.15)
        self.assertEqual(result["lines"][1]["parent_external_line_id"], "burger")
        self.assertEqual(result["payments"][0]["collected_by"], "aggregator")
        self.assertNotIn("untaxed_discounts", result)
        self.assertEqual(self.env["bs.delivery.order"].search_count([]), order_count)
        self.assertEqual(self.env["pos.payment"].search_count([]), payment_count)

    def test_separate_untaxed_discount_reconciles_without_changing_item_tax(self):
        payload = self._payload()
        payload["untaxed_discounts"] = [{"label": "Coupon Discount", "amount": 2}]
        payload["totals"].update(discount_amount=6, amount_total=47.15)
        payload["payments"][0]["amount"] = 47.15
        result = self._validate(payload)
        self.assertEqual(result["untaxed_discounts"][0]["amount"], 2)
        self.assertEqual(result["totals"]["vat_amount"], 6.15)
        self.assertEqual(result["lines"][0]["price_subtotal"], 36)

        for value in (0, -1, 50):
            invalid = deepcopy(payload)
            invalid["untaxed_discounts"][0]["amount"] = value
            with self.subTest(amount=value):
                self._assert_invalid(invalid)

    def test_validation_does_not_mutate_or_alias_input(self):
        payload = self._payload()
        payload["external_order_id"] = " ORDER-1 "
        original = deepcopy(payload)
        result = self._validate(payload)
        self.assertEqual(payload, original)
        self.assertEqual(result["external_order_id"], "ORDER-1")
        result["lines"][0]["name"] = "Changed"
        result["payments"][0]["amount"] = 1
        self.assertEqual(payload, original)

    def test_unknown_fields_and_orm_commands_are_rejected(self):
        for key in ("company_id", "store_binding_id", "pos_order_id", "state", "context"):
            payload = self._payload()
            payload[key] = 1
            with self.subTest(key=key):
                self._assert_invalid(payload)
        payload = self._payload()
        payload["lines"] = [(0, 0, payload["lines"][0])]
        self._assert_invalid(payload)
        payload = self._payload()
        payload["lines"][0]["tax_ids"] = [1]
        self._assert_invalid(payload)

    def test_missing_fields_and_non_objects_are_rejected(self):
        for payload in (None, [], "order", {}):
            with self.subTest(payload=payload):
                self._assert_invalid(payload)
        for key in self._payload():
            if key in {"fees", "payments"}:
                continue
            payload = self._payload()
            del payload[key]
            with self.subTest(key=key):
                self._assert_invalid(payload)

    def test_version_is_integer_one(self):
        for version in (True, 1.0, "1", 2, None):
            payload = self._payload()
            payload["contract_version"] = version
            with self.subTest(version=version):
                self._assert_invalid(payload)

    def test_invalid_numeric_values_are_rejected(self):
        for amount in (True, "20", float("nan"), float("inf"), -1, 10**13, 1e-13):
            payload = self._payload()
            payload["lines"][0]["price_unit"] = amount
            with self.subTest(amount=amount):
                self._assert_invalid(payload)
        for quantity in (0, -1, False):
            payload = self._payload()
            payload["lines"][0]["quantity"] = quantity
            self._assert_invalid(payload)

    def test_line_subtotal_and_discount_must_reconcile(self):
        for field, value in (("price_subtotal", 35), ("discount_amount", 41)):
            payload = self._payload()
            payload["lines"][0][field] = value
            self._assert_invalid(payload)

    def test_each_total_must_reconcile_without_balancing_rounding(self):
        for key in self._payload()["totals"]:
            payload = self._payload()
            payload["totals"][key] += 0.01
            with self.subTest(key=key):
                self._assert_invalid(payload)

    def test_duplicate_line_ids_and_invalid_modifier_parents(self):
        for parent in ("missing", "cheese", 1, ""):
            payload = self._payload()
            payload["lines"][1]["parent_external_line_id"] = parent
            self._assert_invalid(payload)
        payload = self._payload()
        payload["lines"][0]["parent_external_line_id"] = "cheese"
        self._assert_invalid(payload)
        payload = self._payload()
        payload["lines"][1]["external_line_id"] = "burger"
        self._assert_invalid(payload)

    def test_included_modifier_has_zero_financial_contribution(self):
        payload = self._payload()
        payload["lines"][1].update(price_unit=0, price_subtotal=0, vat_amount=0)
        payload["totals"].update(items_subtotal=40, vat_amount=5.85, amount_total=46.85)
        payload["payments"][0]["amount"] = 46.85
        self.assertEqual(self._validate(payload)["totals"]["amount_total"], 46.85)

    def test_money_precision_and_tax_basis_are_explicit(self):
        payload = self._payload()
        payload["fees"][0]["vat_amount"] = 0.451
        self._assert_invalid(payload)
        payload = self._payload()
        payload["price_basis"] = "tax_included"
        self._assert_invalid(payload)
        payload = self._payload()
        payload["fees"].append(deepcopy(payload["fees"][0]))
        self._assert_invalid(payload)

    def test_fractional_quantity_and_half_up_unit_price_rounding(self):
        payload = self._payload()
        payload["lines"] = [{
            "external_line_id": "weighted", "external_product_id": "SKU-W",
            "name": "Weighted item", "quantity": 0.5, "price_unit": 0.21,
            "discount_amount": 0, "vat_amount": 0, "price_subtotal": 0.11,
        }]
        payload.pop("fees")
        payload.pop("payments")
        payload["totals"].update(items_subtotal=0.11, discount_amount=0,
                                 fee_amount=0, vat_amount=0, amount_total=0.11)
        self.assertEqual(self._validate(payload)["totals"]["amount_total"], 0.11)

    def test_order_and_payment_currency_must_match_trusted_currency(self):
        payload = self._payload()
        payload["currency"] = "EUR"
        self._assert_invalid(payload)
        payload = self._payload()
        payload["payments"][0]["currency"] = "EUR"
        self._assert_invalid(payload)

    def test_collected_payment_requires_reference_and_known_collector(self):
        for change in ({"collected_by": "unknown"}, {"external_payment_reference": ""}):
            payload = self._payload()
            payload["payments"][0].update(change)
            self._assert_invalid(payload)
        payload = self._payload()
        del payload["payments"][0]["external_payment_reference"]
        self._assert_invalid(payload)

    def test_expected_payment_is_not_evidence_of_collection(self):
        payload = self._payload()
        payment = payload["payments"][0]
        payment.update(collected_by="unknown", collection_status="expected", external_payment_code="0")
        del payment["external_payment_reference"]
        result = self._validate(payload)["payments"][0]
        self.assertEqual(result["collection_status"], "expected")
        self.assertEqual(result["external_payment_code"], "0")
        self.assertNotIn("external_payment_reference", result)

    def test_partial_and_split_payments_are_allowed_but_overpayment_is_not(self):
        payload = self._payload()
        payload["payments"][0]["amount"] = 20
        self._validate(payload)
        second = deepcopy(payload["payments"][0])
        second.update(amount=29.15, external_payment_reference="PAY-2", collected_by="restaurant")
        payload["payments"].append(second)
        self._validate(payload)
        payload["payments"][1]["amount"] = 30
        self._assert_invalid(payload)

    def test_duplicate_payment_reference_is_rejected(self):
        payload = self._payload()
        payload["payments"][0]["amount"] = 10
        payload["payments"].append(deepcopy(payload["payments"][0]))
        self._assert_invalid(payload)

    def test_standalone_payment_has_same_validation_rules(self):
        payment = self._payload()["payments"][0]
        for key, value in (("amount", 0), ("amount", True), ("collection_status", "paid"),
                           ("collected_by", "visa"), ("external_payment_code", 0)):
            invalid = {**payment, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(DeliveryBusinessError):
                self.Contract._validate_payment_payload(invalid, currency=self.currency)

    def test_status_update_normalizes_time_and_keeps_pos_completion_separate(self):
        payload = {"contract_version": 1, "state": "ready", "occurred_at": "2026-10-01T15:00:00+03:00"}
        result = self.Contract._validate_provider_update(payload)
        self.assertEqual(result["occurred_at"], "2026-10-01T12:00:00+00:00")
        for state in ("completed", "paid", "done"):
            with self.subTest(state=state), self.assertRaises(DeliveryBusinessError):
                self.Contract._validate_provider_update({**payload, "state": state})
        with self.assertRaises(DeliveryBusinessError):
            self.Contract._validate_provider_update({**payload, "pos_processing_state": "done"})

    def test_timestamps_require_timezone_and_customer_fields_are_limited(self):
        for timestamp in ("2026-10-01T12:00:00", "not-a-date", False):
            payload = self._payload()
            payload["scheduled_at"] = timestamp
            self._assert_invalid(payload)
        payload = self._payload()
        payload["scheduled_at"] = "2026-10-01T12:00:00Z"
        payload["customer"] = {"name": " Customer ", "note": ""}
        result = self._validate(payload)
        self.assertEqual(result["scheduled_at"], "2026-10-01T12:00:00+00:00")
        self.assertEqual(result["customer"]["name"], "Customer")
        payload["customer"]["partner_id"] = 1
        self._assert_invalid(payload)
