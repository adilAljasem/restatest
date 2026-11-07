from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from odoo.addons.bs_hr_ess_base.tests.common import EssCommon


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestDisciplinary(EssCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.member = new_test_user(cls.env, login='dis_member',
                                   groups='bs_hr_ess_base.group_ess_grievance_committee')
        cls.vtype = cls.env['bs.hr.violation.type'].create({
            'name': 'Test lateness', 'code': 'TLATE',
            'first_action': 'written_warning',
            'second_action': 'deduction_days', 'second_value': 1,
            'third_action': 'deduction_amount', 'third_value': 150,
        })

    def _new(self, days_ago=0, **vals):
        return self.env['bs.hr.disciplinary'].with_user(self.user_hr).create({
            'employee_id': self.emp_employee.id, 'violation_type_id': self.vtype.id,
            'date': fields.Date.today() - timedelta(days=days_ago), 'description': 'Late', **vals})

    def test_ladder_and_adjustment(self):
        first = self._new(days_ago=30)
        self.assertEqual(first.occurrence, 1)
        self.assertEqual(first.action, 'written_warning')
        self.assertEqual(first.deduction_amount, 0)
        first.action_confirm()
        self.assertFalse(first.adjustment_id)

        second = self._new(days_ago=10)
        self.assertEqual(second.occurrence, 2)
        self.assertEqual(second.action, 'deduction_days')
        self.assertAlmostEqual(second.deduction_amount, 6000 / 30 * 1)
        second.action_notify_employee()
        self.assertEqual(second.state, 'notified')
        self.assertTrue(self.env['bs.hr.notification'].search(
            [('employee_id', '=', self.emp_employee.id), ('res_model', '=', second._name), ('res_id', '=', second.id)]))
        second.action_confirm()
        adj = second.adjustment_id
        self.assertTrue(adj)
        self.assertEqual(adj.category, 'disciplinary')
        self.assertEqual(adj.amount, 200.0)
        self.assertEqual(adj.employee_id, self.emp_employee)
        self.assertEqual((adj.source_model, adj.source_id), (second._name, second.id))

        third = self._new()
        self.assertEqual(third.occurrence, 3)
        self.assertEqual(third.action, 'deduction_amount')
        self.assertEqual(third.deduction_amount, 150)

        # violations older than 180 days do not count
        old = self._new(days_ago=400)
        self.assertEqual(old.occurrence, 1)

        # cancel removes the pending deduction
        second.action_cancel()
        self.assertEqual(adj.state, 'cancelled')

    def test_appeal_creates_grievance(self):
        rec = self._new()
        rec.action_notify_employee()
        self.assertTrue(rec._ess_can_appeal())
        grievance = rec.sudo()._ess_appeal('I was on an approved permission')
        self.assertEqual(rec.state, 'appealed')
        self.assertEqual(grievance.grievance_type, 'appeal_disciplinary')
        self.assertFalse(grievance.anonymous)
        self.assertEqual(grievance.employee_id, self.emp_employee)
        self.assertEqual((grievance.related_res_model, grievance.related_res_id), (rec._name, rec.id))
        # the HR officer who issued it and the branch manager are parties
        self.assertIn(self.user_hr, grievance.excluded_user_ids)
        self.assertIn(self.user_manager, grievance.excluded_user_ids)
        self.assertIn(self.member, grievance.handler_ids)
        with self.assertRaises(UserError):
            rec.action_confirm()
        # committee accepts the appeal -> action cancelled
        grievance.with_user(self.member).write({'resolution': 'Permission verified'})
        grievance.with_user(self.member).action_resolve()
        self.assertEqual(rec.state, 'cancelled')

    def test_appeal_rejected_back_to_notified(self):
        rec = self._new()
        rec.action_notify_employee()
        grievance = rec.sudo()._ess_appeal('Unfair')
        grievance.with_user(self.member).write({'resolution': 'Facts confirmed'})
        grievance.with_user(self.member).action_reject()
        self.assertEqual(rec.state, 'notified')
        self.assertFalse(grievance._can_appeal())  # disciplinary appeals have a single level

    def test_appeal_deadline(self):
        rec = self._new()
        rec.action_notify_employee()
        rec.appeal_deadline = fields.Date.today() - timedelta(days=1)
        self.assertFalse(rec._ess_can_appeal())
        with self.assertRaises(UserError):
            rec.sudo()._ess_appeal('Late appeal')

    def test_acknowledge(self):
        rec = self._new()
        rec.action_notify_employee()
        rec._ess_acknowledge()
        self.assertTrue(rec.employee_ack_date)
