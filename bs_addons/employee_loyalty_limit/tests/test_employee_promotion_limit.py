from datetime import date
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestEmployeePromotionLimit(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Employee Customer"})
        cls.employee = cls.env["hr.employee"].create({
            "name": "Employee A",
            "work_contact_id": cls.partner.id,
            "company_id": cls.env.company.id,
        })
        cls.program = cls.env["loyalty.program"].create({
            "name": "Employee 20% Discount",
            "program_type": "promotion",
            "for_employees": True,
            "employee_limit": 5,
            "employee_limit_period": "week",
        })

    def test_period_bounds(self):
        self.assertEqual(
            self.program._employee_period_bounds(date(2026, 8, 19)),
            (date(2026, 8, 17), date(2026, 8, 24)),
        )
        self.program.employee_limit_period = "month"
        self.assertEqual(
            self.program._employee_period_bounds(date(2026, 8, 19)),
            (date(2026, 8, 1), date(2026, 9, 1)),
        )

    def test_non_employee_is_rejected(self):
        customer = self.env["res.partner"].create({"name": "Regular Customer"})
        with self.assertRaisesRegex(ValidationError, "available only to employees"):
            self.program._validate_employee_promotion(customer, date(2026, 8, 19))

    def test_customer_resolves_to_employee(self):
        self.assertEqual(
            self.program._employee_for_partner(self.partner, self.env.company),
            self.employee,
        )

    def test_reached_limit_is_rejected(self):
        with patch.object(
            type(self.program), "_employee_usage_count", return_value=5
        ), self.assertRaisesRegex(ValidationError, "limit has been reached"):
            self.program._validate_employee_promotion(
                self.partner, date(2026, 8, 20), self.env.company
            )

    def test_department_domain(self):
        department = self.env["hr.department"].create({"name": "Promotion Department"})
        self.program.employee_domain = repr([("department_id", "=", department.id)])
        with self.assertRaisesRegex(ValidationError, "employee conditions"):
            self.program._validate_employee_promotion(self.partner)
        self.employee.department_id = department
        self.assertEqual(self.program._validate_employee_promotion(self.partner), self.employee)

    def test_tags_domain_and_empty_domain(self):
        tag = self.env["hr.employee.category"].create({"name": "Promotion Eligible"})
        self.program.employee_domain = repr([("category_ids", "in", tag.ids)])
        self.assertFalse(self.program._employee_matches_domain(self.employee))
        self.employee.category_ids = [(4, tag.id)]
        self.assertEqual(self.program._validate_employee_promotion(self.partner), self.employee)
        self.program.employee_domain = "[]"
        self.employee.category_ids = [(5, 0, 0)]
        self.assertTrue(self.program._employee_matches_domain(self.employee))

    def test_combined_domain(self):
        department = self.env["hr.department"].create({"name": "Eligible Department"})
        tag = self.env["hr.employee.category"].create({"name": "Eligible Tag"})
        self.program.employee_domain = repr([
            "|", ("department_id", "=", department.id), ("category_ids", "in", tag.ids),
        ])
        self.assertFalse(self.program._employee_matches_domain(self.employee))
        self.employee.category_ids = [(4, tag.id)]
        self.assertTrue(self.program._employee_matches_domain(self.employee))

    def test_pos_eligibility_and_server_validation(self):
        self.program.employee_domain = repr([("id", "!=", self.employee.id)])
        config = self.env["pos.config"].create({"name": "Employee Domain Test"})
        order_model = self.env["pos.order"]
        status = order_model.get_employee_promotion_status(
            self.partner.id, self.program.ids, config.id,
        )
        self.assertFalse(status["eligible"][str(self.program.id)])
        result = order_model.validate_employee_promotion_programs(
            self.partner.id, self.program.ids, config.id,
        )
        self.assertFalse(result["successful"])
        program = self.program.with_context(
            employee_usage_partner_ids=tuple(self.partner.ids),
            employee_usage_company_id=self.env.company.id,
        )
        self.assertFalse(program.employee_eligible_by_partner[str(self.partner.id)])
        self.program.employee_domain = "[]"
        self.assertTrue(program.employee_eligible_by_partner[str(self.partner.id)])

    def test_invalid_employee_domain(self):
        for domain in ("[", "{}", "[('missing_employee_field', '=', 1)]"):
            with self.subTest(domain=domain), self.assertRaises(ValidationError):
                with self.env.cr.savepoint():
                    self.program.employee_domain = domain
