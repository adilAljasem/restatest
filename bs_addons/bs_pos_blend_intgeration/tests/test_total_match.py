from odoo.tests import tagged

from .test_order_accounting import BlendAccountingCommon


@tagged("post_install", "-at_install", "blend_total_match")
class TestBlendTotalMatch(BlendAccountingCommon):
    def test_match_mismatch_and_correction(self):
        order = self._create()
        self.assertTrue(order.blend_total_matches)
        self.assertFalse(order.blend_total_warning)
        original_payload = dict(order.blend_payload)
        order.amount_total = 63
        self.assertFalse(order.blend_total_matches)
        self.assertIn("ZATCA", order.blend_total_warning)
        self.assertIn(order.blend_partner_reference, order.blend_total_warning)
        self.assertEqual(order.blend_total_difference, 1)
        order.amount_total = 62
        self.assertTrue(order.blend_total_matches)
        self.assertFalse(order.blend_total_warning)
        self.assertEqual(order.blend_payload, original_payload)

    def test_currency_precision(self):
        order = self._create()
        order.amount_total = 62.000000001
        self.assertTrue(order.blend_total_matches)
        order.amount_total = 62 + order.currency_id.rounding
        self.assertFalse(order.blend_total_matches)

    def test_payload_changes_and_invalid_total(self):
        order = self._create()
        for payload in ({}, {"total": None}, {"total": "NaN"}, {"total": False}):
            order.blend_payload = payload
            self.assertFalse(order.blend_total_matches)
            self.assertTrue(order.blend_total_warning)
        order.blend_payload = {"total": "62"}
        self.assertTrue(order.blend_total_matches)
        order.write({"amount_total": 0, "blend_payload": {"total": 0}})
        self.assertTrue(order.blend_total_matches)

    def test_non_blend_orders_are_unaffected(self):
        order = self._create()
        order.write({"blend_partner_reference": False, "blend_payload": False})
        self.assertTrue(order.blend_total_matches)
        self.assertFalse(order.blend_total_warning)

    def test_legacy_inbound_and_multiple_records(self):
        matching = self._create()
        mismatching = self._create()
        mismatching.write({"blend_financial_required": False, "amount_total": 65})
        (matching | mismatching)._compute_blend_total_match()
        self.assertTrue(matching.blend_total_matches)
        self.assertFalse(mismatching.blend_total_matches)
