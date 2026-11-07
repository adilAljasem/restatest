import datetime

from odoo.exceptions import AccessDenied
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..models.otp import normalize_phone
from ..tools import compute_eos, to_hijri_str
from ..tools.hijri import to_hijri


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssTools(TransactionCase):

    def test_eos(self):
        # 10 years, termination: 5 * 0.5 + 5 * 1 = 7.5 months
        res = compute_eos(10000, 3650, 'termination')
        self.assertAlmostEqual(res['amount'], 75000, delta=1)
        # resignation after 3 years -> one third of 1.5 months
        res = compute_eos(9000, 365 * 3, 'resignation')
        self.assertAlmostEqual(res['amount'], 9000 * 1.5 / 3, delta=1)
        self.assertEqual(compute_eos(9000, 300, 'resignation')['amount'], 0)

    def test_hijri(self):
        self.assertEqual(to_hijri(datetime.date(2026, 11, 3)), (1448, 5, 23))
        self.assertIn('1448', to_hijri_str(datetime.date(2026, 11, 3), 'ar_001'))

    def test_phone(self):
        self.assertEqual(normalize_phone('0551234567'), '966551234567')
        self.assertEqual(normalize_phone('+966 55 123 4567'), '966551234567')
        self.assertEqual(normalize_phone('00966551234567'), '966551234567')

    def test_otp_credential(self):
        user = self.env['res.users'].create({'name': 'otp', 'login': 'otp_user', 'group_ids': [
            (6, 0, self.env.ref('base.group_portal').ids)]})
        employee = self.env['hr.employee'].create({'name': 'otp', 'user_id': user.id,
                                                   'mobile_phone': '0551112233'})
        self.env.company.ess_otp_login = True
        self.assertEqual(self.env['bs.hr.ess.otp']._find_employee_by_phone('+966551112233'), employee)
        otp, err = self.env['bs.hr.ess.otp']._issue(employee, '1.2.3.4')
        self.assertFalse(err)
        with self.assertRaises(AccessDenied):
            user.with_user(user)._check_credentials(
                {'type': 'bs_ess_otp', 'otp_id': otp.id, 'code': '000000x'}, {})
