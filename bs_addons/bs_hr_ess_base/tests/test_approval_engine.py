from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import EssCommon


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestApprovalEngine(EssCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.flow = cls.env['bs.hr.approval.flow'].create({
            'name': 'Test flow', 'res_model': 'bs.hr.profile.change', 'sequence': 1,
            'step_ids': [
                (0, 0, {'name': 'Manager', 'approver_type': 'manager', 'sequence': 1, 'sla_hours': 24}),
                (0, 0, {'name': 'HR', 'approver_type': 'users', 'user_ids': [(6, 0, cls.user_hr.ids)],
                        'sequence': 2}),
                (0, 0, {'name': 'Only for IBAN', 'approver_type': 'users',
                        'user_ids': [(6, 0, cls.user_hr.ids)], 'sequence': 3,
                        'condition_domain': "[('new_iban', '!=', False)]"}),
            ],
        })

    def _new_request(self, **vals):
        return self.env['bs.hr.profile.change'].create({
            'employee_id': self.emp_employee.id, 'new_mobile': '0500000000', **vals})

    def test_full_chain(self):
        req = self._new_request()
        req.action_submit()
        self.assertEqual(req.state, 'in_approval')
        lines = req._ess_lines()
        # conditional IBAN step is skipped
        self.assertEqual(lines.mapped('name'), ['Manager', 'HR'])
        self.assertEqual(req._ess_current_line().approver_ids, self.user_manager)
        self.assertTrue(req._ess_current_line().deadline)
        # HR cannot approve the manager step (not an approver, not HR manager)
        with self.assertRaises(AccessError):
            req.with_user(self.user_hr).action_ess_approve()
        req.with_user(self.user_manager).sudo().action_ess_approve('ok')
        self.assertEqual(req._ess_current_line().name, 'HR')
        req.with_user(self.user_hr).sudo().action_ess_approve()
        # profile change is auto-done and applied on the employee
        self.assertEqual(req.state, 'done')
        self.assertEqual(self.emp_employee.private_phone, '0500000000')
        self.assertTrue(self.env['bs.hr.notification'].search([('employee_id', '=', self.emp_employee.id)]))

    def test_conditional_step(self):
        req = self._new_request(new_iban='SA0380000000608010167519')
        req.action_submit()
        self.assertEqual(len(req._ess_lines()), 3)

    def test_reject(self):
        req = self._new_request()
        req.action_submit()
        req.with_user(self.user_manager).sudo().action_ess_reject('No')
        self.assertEqual(req.state, 'rejected')
        self.assertEqual(req.rejection_reason, 'No')
        self.assertEqual(set(req._ess_lines().mapped('state')), {'rejected', 'cancelled'})
        req.action_reset_draft()
        self.assertEqual(req.state, 'draft')
        self.assertFalse(req._ess_lines())

    def test_no_self_approval_and_skip_missing_manager(self):
        # the manager submits his own request: manager step has no approver -> skipped
        req = self.env['bs.hr.profile.change'].create({
            'employee_id': self.emp_manager.id, 'new_mobile': '0511111111'})
        req.action_submit()
        self.assertEqual(req._ess_lines().mapped('name'), ['HR'])
        self.assertNotIn(self.user_manager, req._ess_lines().approver_ids)

    def test_delegation(self):
        delegate = self.user_hr
        self.env['bs.hr.approval.delegation'].create({
            'user_id': self.user_manager.id, 'delegate_id': delegate.id,
            'date_from': fields.Date.today(), 'date_to': fields.Date.today() + timedelta(days=3)})
        req = self._new_request()
        req.action_submit()
        self.assertIn(delegate, req._ess_current_line().approver_ids)
        req.with_user(delegate).sudo().action_ess_approve()
        self.assertEqual(req._ess_current_line().name, 'HR')

    def test_escalation(self):
        req = self._new_request()
        req.action_submit()
        line = req._ess_current_line()
        line.deadline = fields.Datetime.now() - timedelta(hours=1)
        self.env['bs.hr.approval.line']._cron_escalate()
        self.assertTrue(line.escalated)
        self.assertTrue(line.sla_breached or line.deadline < fields.Datetime.now())

    def test_cannot_submit_twice_or_empty(self):
        req = self.env['bs.hr.profile.change'].create({'employee_id': self.emp_employee.id})
        with self.assertRaises(Exception):
            req.action_submit()
        req.new_mobile = '0599999999'
        req.action_submit()
        with self.assertRaises(UserError):
            req.action_submit()

    def test_fallback_without_flow(self):
        self.flow.active = False
        self.env.ref('bs_hr_ess_base.flow_profile_change').active = False
        req = self._new_request()
        req.action_submit()
        self.assertEqual(req._ess_current_line().approver_ids, self.user_manager)

    def test_adjustment_ledger(self):
        req = self._new_request()
        adj = self.env['bs.hr.payroll.adjustment']._ess_create_from(
            req, self.emp_employee, 250.0, 'Test', 'other')
        self.assertEqual(adj.signed_amount, -250.0)
        self.assertEqual(adj.source_ref, req.display_name)
        adj.action_mark_applied()
        with self.assertRaises(UserError):
            adj.action_cancel()
