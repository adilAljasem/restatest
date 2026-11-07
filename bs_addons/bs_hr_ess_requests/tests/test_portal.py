import base64
from datetime import date, timedelta

from odoo.tests import HttpCase, new_test_user, tagged

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssRequestsPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_a = new_test_user(cls.env, login='ess_req_a', password='ess_req_a', groups='base.group_portal')
        cls.user_b = new_test_user(cls.env, login='ess_req_b', password='ess_req_b', groups='base.group_portal')
        cls.manager_user = new_test_user(cls.env, login='ess_req_mgr', password='ess_req_mgr',
                                         groups='bs_hr_ess_base.group_ess_branch_supervisor')
        cls.emp_mgr = cls.env['hr.employee'].create({'name': 'Portal Manager', 'user_id': cls.manager_user.id})
        cls.emp_a = cls.env['hr.employee'].create({
            'name': 'Portal A', 'user_id': cls.user_a.id, 'wage': 5000, 'contract_date_start': '2019-01-01',
            'parent_id': cls.emp_mgr.id})
        cls.emp_b = cls.env['hr.employee'].create({
            'name': 'Portal B', 'user_id': cls.user_b.id, 'wage': 5000, 'contract_date_start': '2019-01-01'})
        cls.loan_type = cls.env['bs.hr.loan.type'].create({
            'name': 'Portal loan', 'max_percent_wage': 100, 'max_installments': 5, 'min_service_months': 0,
            'allow_parallel': True})
        cls.leave_type = cls.env['hr.leave.type'].create({
            'name': 'Portal leave', 'requires_allocation': False, 'leave_validation_type': 'hr'})
        cls.product = cls.env['product.product'].create({
            'name': 'Portal tablet', 'type': 'consu', 'ess_is_custody': True})
        cls.loan_b = cls.env['bs.hr.loan'].create({
            'employee_id': cls.emp_b.id, 'loan_type_id': cls.loan_type.id, 'amount': 1000, 'installment_count': 2})
        cls.leave_b = cls.env['bs.hr.leave.request'].create({
            'employee_id': cls.emp_b.id, 'leave_type_id': cls.leave_type.id,
            'date_from': date.today() + timedelta(days=30), 'date_to': date.today() + timedelta(days=31)})
        cls.custody_b = cls.env['bs.hr.custody.request'].create({
            'employee_id': cls.emp_b.id, 'request_type': 'new',
            'line_ids': [(0, 0, {'product_id': cls.product.id})]})
        cls.holding_b = cls.env['bs.hr.custody.holding'].create({
            'employee_id': cls.emp_b.id, 'product_id': cls.product.id})
        cls.rtype = cls.env.ref('bs_hr_ess_requests.request_type_to_whom')
        cls.generic_b = cls.env['bs.hr.request'].create({
            'employee_id': cls.emp_b.id, 'type_id': cls.rtype.id, 'values': {'purpose': 'Bank'}})

    def csrf_token(self):
        return self.opener.get(self.base_url() + '/my/hr').text.split('csrf_token: "')[1].split('"')[0]

    def test_pages_render(self):
        self.authenticate('ess_req_a', 'ess_req_a')
        urls = ['/my/hr', '/my/hr/new', '/my/hr/loans', '/my/hr/loan/new',
                '/my/hr/loan/new?loan_type_id=%s' % self.loan_type.id, '/my/hr/leaves', '/my/hr/leave/new',
                '/my/hr/custody', '/my/hr/custody/new', '/my/hr/custody/new?type=loss',
                '/my/hr/expenses', '/my/hr/expense/new', '/my/hr/request/new/%s' % self.rtype.id,
                '/my/hr/request/new/%s' % self.env.ref('bs_hr_ess_requests.request_type_housing').id]
        for url in urls:
            res = self.url_open(url)
            self.assertEqual(res.status_code, 200, url)
        # menu entries from this module are on the "+" page
        self.assertIn('/my/hr/loan/new', self.url_open('/my/hr/new').text)

    def test_cannot_open_other_employee_records(self):
        self.authenticate('ess_req_a', 'ess_req_a')
        for url in ('/my/hr/loan/%s' % self.loan_b.id,
                    '/my/hr/request/bs.hr.loan/%s' % self.loan_b.id,
                    '/my/hr/request/bs.hr.leave.request/%s' % self.leave_b.id,
                    '/my/hr/request/bs.hr.custody.request/%s' % self.custody_b.id,
                    '/my/hr/request/bs.hr.request/%s' % self.generic_b.id,
                    '/my/hr/letter/%s' % self.generic_b.id):
            self.assertEqual(self.url_open(url).status_code, 404, url)
        token = self.csrf_token()
        res = self.url_open('/my/hr/custody/%s/ack' % self.holding_b.id, data={'csrf_token': token})
        self.assertEqual(res.status_code, 404)
        self.assertFalse(self.holding_b.ack_date)

    def test_loan_flow_from_portal(self):
        self.authenticate('ess_req_a', 'ess_req_a')
        token = self.csrf_token()
        res = self.url_open('/my/hr/loan/new', data={
            'csrf_token': token, 'loan_type_id': self.loan_type.id, 'amount': '3000',
            'installment_count': '3', 'action': 'preview'})
        self.assertEqual(res.status_code, 200)
        self.assertIn('1,000.00', res.text)
        self.assertFalse(self.env['bs.hr.loan'].search([('employee_id', '=', self.emp_a.id)]))
        res = self.url_open('/my/hr/loan/new', data={
            'csrf_token': token, 'loan_type_id': self.loan_type.id, 'amount': '3000',
            'installment_count': '3', 'action': 'submit'})
        self.assertEqual(res.status_code, 200)
        loan = self.env['bs.hr.loan'].search([('employee_id', '=', self.emp_a.id)])
        self.assertEqual(loan.state, 'in_approval')
        self.assertEqual(self.url_open(loan._ess_portal_url()).status_code, 200)
        # over the limit
        self.url_open('/my/hr/loan/new', data={
            'csrf_token': token, 'loan_type_id': self.loan_type.id, 'amount': '99999',
            'installment_count': '1', 'action': 'submit'})
        self.assertEqual(self.env['bs.hr.loan'].search_count([('employee_id', '=', self.emp_a.id)]), 1)
        # postponement button on an approved loan
        while loan.state == 'in_approval':
            loan.with_user(self.env.ref('base.user_admin')).action_ess_approve()
        res = self.url_open(loan._ess_portal_url())
        self.assertIn('Request postponement', res.text)
        self.url_open('/my/hr/loan/line/%s/postpone' % loan.line_ids[0].id,
                      data={'csrf_token': token, 'reason': 'Rent'})
        self.assertTrue(self.env['bs.hr.loan.postpone'].search([('loan_line_id', '=', loan.line_ids[0].id)]))
        # cannot postpone someone else's installment
        loan_b = self.loan_b
        loan_b.action_submit()
        while loan_b.state == 'in_approval':
            loan_b.with_user(self.env.ref('base.user_admin')).action_ess_approve()
        res = self.url_open('/my/hr/loan/line/%s/postpone' % loan_b.line_ids[0].id,
                            data={'csrf_token': token, 'reason': 'x'})
        self.assertEqual(res.status_code, 404)

    def test_leave_custody_expense_generic_from_portal(self):
        self.authenticate('ess_req_a', 'ess_req_a')
        token = self.csrf_token()
        start = date.today() + timedelta(days=40)
        res = self.url_open('/my/hr/leave/new', data={
            'csrf_token': token, 'leave_type_id': self.leave_type.id,
            'date_from': start.isoformat(), 'date_to': (start + timedelta(days=1)).isoformat(),
            'need_ticket': '1'})
        self.assertEqual(res.status_code, 200)
        leave_req = self.env['bs.hr.leave.request'].search([('employee_id', '=', self.emp_a.id)])
        self.assertTrue(leave_req.leave_id)
        self.assertTrue(leave_req.need_ticket)
        self.assertEqual(self.url_open(leave_req._ess_portal_url()).status_code, 200)

        self.url_open('/my/hr/custody/new', data={
            'csrf_token': token, 'request_type': 'new', 'product_id': self.product.id, 'quantity': '1'})
        custody = self.env['bs.hr.custody.request'].search([('employee_id', '=', self.emp_a.id)])
        self.assertEqual(custody.state, 'in_approval')

        holding = self.env['bs.hr.custody.holding'].create({'employee_id': self.emp_a.id,
                                                            'product_id': self.product.id})
        self.url_open('/my/hr/custody/%s/ack' % holding.id, data={'csrf_token': token})
        self.assertTrue(holding.ack_date)

        product = self.env['product.product'].search([('can_be_expensed', '=', True),
                                                      ('standard_price', '=', 0)], limit=1)
        self.assertTrue(product)
        res = self.url_open('/my/hr/expense/new', data={
            'csrf_token': token, 'product_id': product.id, 'amount': '45.5',
            'description': 'Taxi to supplier'}, files={'receipt': ('receipt.png', PNG, 'image/png')})
        self.assertEqual(res.status_code, 200)
        expense = self.env['hr.expense'].search([('employee_id', '=', self.emp_a.id)])
        self.assertEqual(len(expense), 1)
        self.assertEqual(expense.total_amount_currency, 45.5)
        self.assertNotEqual(expense.state, 'draft')
        self.assertTrue(expense.attachment_ids)
        self.assertIn('Taxi to supplier', self.url_open('/my/hr/expenses').text)

        # generic request: required field validated server-side
        self.url_open('/my/hr/request/new/%s' % self.rtype.id, data={'csrf_token': token, 'f_purpose': ''})
        self.assertFalse(self.env['bs.hr.request'].search([('employee_id', '=', self.emp_a.id)]))
        self.url_open('/my/hr/request/new/%s' % self.rtype.id, data={'csrf_token': token, 'f_purpose': 'Embassy'})
        generic = self.env['bs.hr.request'].search([('employee_id', '=', self.emp_a.id)])
        self.assertEqual(generic.values, {'purpose': 'Embassy'})
        self.assertEqual(self.url_open(generic._ess_portal_url()).status_code, 200)

    def test_letter_verify_public(self):
        req = self.env['bs.hr.request'].create({
            'employee_id': self.emp_a.id, 'type_id': self.rtype.id, 'values': {'purpose': 'Visa'}})
        req.action_submit()
        while req.state == 'in_approval':
            req.with_user(self.env.ref('base.user_admin')).action_ess_approve()
        self.assertEqual(req.state, 'done')
        token = req.sudo().verify_token
        res = self.url_open('/hr/ess/verify/%s' % token)
        self.assertEqual(res.status_code, 200)
        self.assertIn('Valid document', res.text)
        self.assertIn('Portal A', res.text)
        res = self.url_open('/hr/ess/verify/%s' % ('z' * 32))
        self.assertIn('Unknown or revoked document', res.text)
        self.authenticate('ess_req_a', 'ess_req_a')
        res = self.url_open('/my/hr/letter/%s' % req.id)
        self.assertEqual(res.status_code, 200)
        self.assertIn('Download the letter', self.url_open(req._ess_portal_url()).text)
