"""Pure routing tests; runnable without an Odoo database."""
import runpy
import unittest
from pathlib import Path


class TestOrderTypeRouting(unittest.TestCase):
    def setUp(self):
        self.route = runpy.run_path(str(Path(__file__).parents[1] / "services" / "order_types.py"))["preset_field_for_order_type"]

    def test_supported_types_and_normalized_spelling(self):
        for value, expected in (
            ("delivery", "blend_default_preset_id"),
            (" DELIVERY ", "blend_default_preset_id"),
            ("pickup", "blend_pickup_preset_id"),
            ("Pick-Up", "blend_pickup_preset_id"),
            ("dinein", "blend_dinein_preset_id"),
            ("DINE_IN", "blend_dinein_preset_id"),
            ("dine in", "blend_dinein_preset_id"),
        ):
            with self.subTest(value=value):
                self.assertEqual(self.route(value), expected)

    def test_unknown_or_missing_type_never_falls_back_to_delivery(self):
        for value in ("", None, False, "courier", "takeaway", "delivery2"):
            with self.subTest(value=value):
                self.assertIsNone(self.route(value))


if __name__ == "__main__":
    unittest.main()
