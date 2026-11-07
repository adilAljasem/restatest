"""Order numbers are always 3 digits (server side)."""
import importlib.util
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'models' / 'order_number_range.py'
spec = importlib.util.spec_from_file_location(
    'odoo.addons.odoo_pos_custom_branding.models.order_number_range', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OrderNumberDigitsTest(unittest.TestCase):
    def test_range(self):
        cycle = module.cycle_order_number
        self.assertEqual([cycle(i, 1, 3) for i in range(1, 6)], ['001', '002', '003', '001', '002'])
        self.assertEqual(cycle(1, 100, 200), '100')
        self.assertEqual(cycle(102, 100, 200), '100')

    def test_always_three_digits(self):
        cycle = module.cycle_order_number
        for sequence in range(1, 5000, 17):
            self.assertRegex(cycle(sequence), r'^\d{3}$')
            self.assertRegex(cycle(sequence, 1, 100), r'^\d{3}$')
        self.assertEqual(cycle(999), '999')
        self.assertEqual(cycle(1000), '001')
        # Invalid range (end over 999): plain numbering, still 3 digits.
        self.assertEqual(cycle(5, 1, 5000), '005')


if __name__ == '__main__':
    unittest.main()
