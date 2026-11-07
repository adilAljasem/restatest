from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from odoo.addons.point_of_sale.tests.common import TestPoSCommon


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssPosBridge(TestPoSCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.env = cls.env(su=True)
        partner = cls.env['res.partner'].create({'name': 'POS branch'})
        cls.branch = cls.env['hr.work.location'].create({'name': 'POS branch', 'address_id': partner.id})
        cls.cashier = new_test_user(cls.env, login='ess_cashier', groups='point_of_sale.group_pos_user',
                                    company_id=cls.env.company.id)
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Cashier', 'user_id': cls.cashier.id, 'work_location_id': cls.branch.id,
            'hourly_cost': 25.0})
        cls.config.sudo().write({'ess_work_location_id': cls.branch.id, 'ess_require_attendance': True})

    def test_attendance_required(self):
        with self.assertRaises(UserError):
            self.config.with_user(self.cashier).with_env(self.config.with_user(self.cashier).env(su=False))._ess_check_attendance()
        self.env['hr.attendance'].create({'employee_id': self.employee.id, 'check_in': fields.Datetime.now(),
                                          'ess_location_id': self.branch.id})
        self.config.with_user(self.cashier).with_env(self.config.with_user(self.cashier).env(su=False))._ess_check_attendance()

    def test_labor_cost_report(self):
        now = fields.Datetime.now()
        self.env['hr.attendance'].create({
            'employee_id': self.employee.id, 'check_in': fields.Datetime.subtract(now, hours=8),
            'check_out': now, 'ess_location_id': self.branch.id})
        self.env.flush_all()
        rows = self.env['bs.hr.ess.labor.cost.report'].search([('location_id', '=', self.branch.id)])
        self.assertTrue(rows)
        self.assertAlmostEqual(sum(rows.mapped('labor_cost')), 8 * 25.0, delta=1)
