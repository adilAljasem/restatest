from datetime import date
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import ValidationError


@tagged("post_install", "-at_install")
class TestExclusiveProgram(TransactionCase):
    def setUp(self):
        super().setUp()
        self.customer = self.env["res.partner"].create({"name": "Exclusive Test Customer"})
        self.program = self.env["loyalty.program"].create({
            "name": "Exclusive Test", "program_type": "promotion",
            "exclusive_customer": True, "limit_for_customer": True,
            "exclusive_limit": 2, "exclusive_limit_period": "week",
            "exclusive_domain": repr([("id", "=", self.customer.id)]),
        })

    def test_customer_domain(self):
        self.assertTrue(self.program._exclusive_matches_domain(self.customer))
        other = self.env["res.partner"].create({"name": "Other Customer"})
        self.assertFalse(self.program._exclusive_matches_domain(other))
        with self.assertRaises(ValidationError):
            self.program._validate_exclusive_promotion(other, date(2026, 11, 4))

    def test_week_anchor(self):
        self.program.date_from = date(2026, 11, 3)
        self.assertEqual(self.program._exclusive_period_bounds(date(2026, 11, 9)),
                         (date(2026, 11, 3), date(2026, 11, 10)))
        self.assertEqual(self.program._exclusive_period_bounds(date(2026, 11, 10)),
                         (date(2026, 11, 10), date(2026, 11, 17)))
        self.program.date_from = False
        self.assertEqual(self.program._exclusive_period_bounds(date(2026, 10, 30)),
                         (date(2026, 10, 24), date(2026, 10, 31)))

    def test_positive_limit(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.program.exclusive_limit = 0

    def test_no_customer(self):
        with self.assertRaises(ValidationError):
            self.program._validate_exclusive_promotion(self.env["res.partner"], date(2026, 11, 4))
