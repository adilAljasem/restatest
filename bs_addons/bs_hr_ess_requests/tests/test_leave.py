from datetime import date, timedelta

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.bs_hr_ess_base.tests.common import EssCommon


def _next_monday(days=14):
    d = date.today() + timedelta(days=days)
    return d + timedelta(days=(7 - d.weekday()) % 7)


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssLeave(EssCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_admin = cls.env.ref('base.user_admin')
        cls.leave_type = cls.env['hr.leave.type'].create({
            'name': 'ESS test leave', 'requires_allocation': False, 'leave_validation_type': 'hr',
            'request_unit': 'day'})
        cls.sick_type = cls.env['hr.leave.type'].create({
            'name': 'ESS sick', 'requires_allocation': False, 'leave_validation_type': 'hr',
            'support_document': True})
        cls.annual_type = cls.env['hr.leave.type'].create({
            'name': 'ESS annual', 'requires_allocation': True, 'leave_validation_type': 'hr'})
        cls.monday = _next_monday()

    def _request(self, leave_type=None, start=None, days=2, **vals):
        start = start or self.monday
        return self.env['bs.hr.leave.request'].create({
            'employee_id': self.emp_employee.id, 'leave_type_id': (leave_type or self.leave_type).id,
            'date_from': start, 'date_to': start + timedelta(days=days - 1), **vals})

    def _approve_all(self, record):
        while record.state == 'in_approval':
            record.with_user(self.user_admin).action_ess_approve()

    def test_submit_creates_leave_and_approval_validates(self):
        req = self._request()
        req.action_submit()
        self.assertEqual(req.state, 'in_approval')
        self.assertTrue(req.leave_id)
        self.assertEqual(req.leave_id.state, 'confirm')
        self.assertEqual(req.number_of_days, 2)
        self.assertEqual(req._ess_lines().mapped('name'), ['Direct manager', 'Human Resources'])
        req.with_user(self.user_manager).sudo().action_ess_approve()
        req.with_user(self.user_hr).sudo().action_ess_approve()
        self.assertEqual(req.state, 'approved')
        self.assertEqual(req.leave_id.state, 'validate')

    def test_no_validation_type_waits_for_ess_approval(self):
        self.leave_type.leave_validation_type = 'no_validation'
        req = self._request()
        req.action_submit()
        self.assertEqual(req.leave_id.state, 'confirm')
        self._approve_all(req)
        self.assertEqual(req.leave_id.state, 'validate')

    def test_reject_refuses_leave(self):
        req = self._request()
        req.action_submit()
        req.with_user(self.user_manager).sudo().action_ess_reject('Busy season')
        self.assertEqual(req.state, 'rejected')
        self.assertEqual(req.leave_id.state, 'refuse')

    def test_cancel_cancels_leave(self):
        req = self._request()
        req.action_submit()
        self._approve_all(req)
        req.with_user(self.user_hr).action_cancel()
        self.assertEqual(req.leave_id.state, 'cancel')

    def test_blackout_blocks(self):
        self.env['bs.hr.leave.blackout'].create({
            'name': 'Ramadan', 'date_from': self.monday, 'date_to': self.monday + timedelta(days=10)})
        with self.assertRaises(ValidationError):
            self._request().action_submit()
        # blackout limited to another type does not block
        self.env['bs.hr.leave.blackout'].search([]).leave_type_ids = self.sick_type
        req = self._request()
        req.action_submit()
        self.assertEqual(req.state, 'in_approval')

    def test_attachment_required(self):
        req = self._request(leave_type=self.sick_type)
        with self.assertRaises(ValidationError):
            req.action_submit()
        self.env['ir.attachment'].create({'name': 'report.pdf', 'raw': b'%PDF-1.4',
                                          'res_model': req._name, 'res_id': req.id})
        req.action_submit()
        self.assertTrue(req.leave_id)

    def test_balance_checked_by_odoo(self):
        with self.assertRaises(ValidationError):
            self._request(leave_type=self.annual_type).action_submit()

    def test_overlap_checked_by_odoo(self):
        self._request().action_submit()
        with self.assertRaises(ValidationError):
            self._request(start=self.monday + timedelta(days=1)).action_submit()

    def test_conflict_warning(self):
        self.env.company.ess_leave_max_concurrent = 1
        job = self.env['hr.job'].create({'name': 'ESS cook'})
        (self.emp_employee | self.emp_other).write({'job_id': job.id})
        other = self.env['bs.hr.leave.request'].create({
            'employee_id': self.emp_other.id, 'leave_type_id': self.leave_type.id,
            'date_from': self.monday, 'date_to': self.monday + timedelta(days=4)})
        other.action_submit()
        req = self._request()
        req.action_submit()
        self.assertTrue(req.conflict_warning)
        self.assertIn('ESS Other', req.conflict_warning)

    def test_return_cron(self):
        req = self._request()
        req.action_submit()
        self._approve_all(req)
        req.write({'date_from': date.today() - timedelta(days=10), 'date_to': date.today() - timedelta(days=5)})
        self.env['bs.hr.leave.request']._cron_check_return()
        if 'hr.attendance' in self.env:
            self.assertEqual(req.return_state, 'late')
        else:
            self.assertEqual(req.return_state, 'none')

    def test_balances_helper(self):
        self.env['hr.leave.allocation'].create({
            'employee_id': self.emp_employee.id, 'holiday_status_id': self.annual_type.id,
            'number_of_days': 21, 'date_from': date.today().replace(month=1, day=1),
        }).action_approve()
        balances = dict((lt, info) for lt, info in self.emp_employee._ess_leave_balances())
        self.assertEqual(balances[self.annual_type]['max_leaves'], 21)
        self.assertIn(self.annual_type, self.emp_employee._ess_requestable_leave_types())
        req = self._request(leave_type=self.annual_type)
        req.action_submit()
        self.assertEqual(req.leave_id.state, 'confirm')
