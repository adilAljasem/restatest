from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.bs_hr_ess_base.tests.common import EssCommon


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssLoan(EssCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_admin = cls.env.ref('base.user_admin')
        Account = cls.env['account.account']
        cls.loan_account = Account.create({'code': 'ESSLN1', 'name': 'Employee loans',
                                           'account_type': 'asset_receivable', 'reconcile': True})
        cls.cash_account = Account.create({'code': 'ESSCS1', 'name': 'ESS cash',
                                           'account_type': 'asset_cash'})
        cls.journal = cls.env['account.journal'].create({
            'name': 'ESS loans', 'code': 'ESSL', 'type': 'general',
            'default_account_id': cls.cash_account.id})
        cls.loan_type = cls.env['bs.hr.loan.type'].create({
            'name': 'Test loan', 'max_percent_wage': 200, 'max_amount': 20000, 'max_installments': 6,
            'min_service_months': 3, 'account_id': cls.loan_account.id, 'journal_id': cls.journal.id,
        })
        cls.first_date = fields.Date.today().replace(day=1) + relativedelta(months=1)

    def _loan(self, amount=9000, count=3, **vals):
        return self.env['bs.hr.loan'].create({
            'employee_id': self.emp_employee.id, 'loan_type_id': self.loan_type.id, 'amount': amount,
            'installment_count': count, 'first_installment_date': self.first_date, **vals})

    def _approve_all(self, record):
        while record.state == 'in_approval':
            record.with_user(self.user_admin).action_ess_approve()

    def test_limits(self):
        limits = self.loan_type._ess_limits(self.emp_employee)
        self.assertEqual(limits['max_amount'], 12000.0)  # 200% of 6000
        self.assertTrue(limits['eligible'])
        with self.assertRaises(ValidationError):
            self._loan(amount=15000).action_submit()
        with self.assertRaises(ValidationError):
            self._loan(count=12).action_submit()
        # minimum service
        self.loan_type.min_service_months = 12 * 50
        with self.assertRaises(ValidationError):
            self._loan().action_submit()

    def test_no_parallel_loan(self):
        self._loan().action_submit()
        with self.assertRaises(ValidationError):
            self._loan(amount=1000, count=1).action_submit()

    def test_flow_schedule_disburse(self):
        loan = self._loan()
        loan.action_submit()
        # manager -> HR -> HR manager (amount > 5000)
        self.assertEqual(len(loan._ess_lines()), 3)
        self.assertEqual(loan._ess_current_line().approver_ids, self.user_manager)
        loan.with_user(self.user_manager).sudo().action_ess_approve()
        loan.with_user(self.user_hr).sudo().action_ess_approve()
        self.assertEqual(loan.state, 'in_approval')
        loan.with_user(self.user_admin).action_ess_approve()
        self.assertEqual(loan.state, 'approved')
        self.assertEqual(len(loan.line_ids), 3)
        self.assertEqual(sum(loan.line_ids.mapped('amount')), 9000)
        self.assertEqual(loan.line_ids[1].date, self.first_date + relativedelta(months=1))
        self.assertEqual(loan.remaining_amount, 9000)

        loan.with_user(self.user_hr).action_disburse()
        self.assertTrue(loan.disbursed)
        self.assertEqual(loan.move_id.state, 'posted')
        debit = loan.move_id.line_ids.filtered(lambda l: l.debit)
        self.assertEqual(debit.account_id, self.loan_account)
        self.assertEqual(debit.partner_id, self.emp_employee.work_contact_id)
        adjustments = loan.line_ids.adjustment_id
        self.assertEqual(len(adjustments), 3)
        self.assertEqual(set(adjustments.mapped('category')), {'loan'})
        self.assertEqual(adjustments.sorted('date').mapped('date'), loan.line_ids.mapped('date'))

        # payroll applies the installments -> loan done
        adjustments[0].action_mark_applied()
        self.assertEqual(loan.line_ids[0].state, 'paid')
        self.assertEqual(loan.remaining_amount, 6000)
        adjustments[1:].action_mark_applied()
        self.assertEqual(loan.state, 'done')
        self.assertEqual(loan.remaining_amount, 0)

    def test_small_loan_skips_hr_manager(self):
        loan = self._loan(amount=3000)
        loan.action_submit()
        self.assertEqual(len(loan._ess_lines()), 2)

    def test_postpone(self):
        loan = self._loan()
        loan.action_submit()
        self._approve_all(loan)
        loan.action_disburse()
        first = loan.line_ids[0]
        last_date = loan.line_ids[-1].date
        postpone = self.env['bs.hr.loan.postpone'].create({
            'employee_id': self.emp_employee.id, 'loan_line_id': first.id, 'reason': 'Family event'})
        postpone.action_submit()
        # a second postponement of the same installment is refused
        with self.assertRaises(ValidationError):
            self.env['bs.hr.loan.postpone'].create({
                'employee_id': self.emp_employee.id, 'loan_line_id': first.id, 'reason': 'x'}).action_submit()
        self._approve_all(postpone)
        self.assertEqual(postpone.state, 'done')
        self.assertEqual(first.state, 'postponed')
        self.assertEqual(first.date, last_date + relativedelta(months=1))
        self.assertEqual(first.adjustment_id.date, first.date)
        self.assertEqual(loan.remaining_amount, 9000)
        first.adjustment_id.action_mark_applied()
        self.assertEqual(first.state, 'paid')

    def test_clearance_blocked_by_loan(self):
        loan = self._loan()
        loan.action_submit()
        self._approve_all(loan)
        clearance = self.env['bs.hr.clearance'].create({'employee_id': self.emp_employee.id})
        clearance.action_check()
        self.assertEqual(clearance.state, 'blocked')
        self.assertIn('loan', clearance.blocker_ids.mapped('category'))
        with self.assertRaises(Exception):
            clearance.action_complete()
        loan.action_disburse()
        loan.line_ids.adjustment_id.action_mark_applied()
        clearance.action_complete()
        self.assertEqual(clearance.state, 'done')
