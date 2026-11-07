"""Payload validation tests runnable without an Odoo database."""
import runpy
import unittest
from pathlib import Path


class TestBlendPayloadTotal(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / "services" / "totals.py"
        self.assertTrue(path.is_file(), "Blend total validation is not implemented")
        self.parse = runpy.run_path(str(path))["blend_payload_total"]

    def test_valid_totals_including_zero(self):
        for value, expected in ((0, 0.0), (115, 115.0), ("115.25", 115.25), ("0", 0.0)):
            with self.subTest(value=value):
                self.assertEqual(self.parse({"total": value}), expected)

    def test_missing_or_invalid_totals_fail_closed(self):
        for payload in (None, False, [], "invalid", {}, {"total": None},
                        {"total": False}, {"total": True}, {"total": ""},
                        {"total": "bad"}, {"total": []}, {"total": {}},
                        {"total": "NaN"}, {"total": "Infinity"},
                        {"total": "-Infinity"}, {"total": 10 ** 400}):
            with self.subTest(payload=payload):
                self.assertIsNone(self.parse(payload))


if __name__ == "__main__":
    unittest.main()
