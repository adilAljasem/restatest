from datetime import date

from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.bs_hr_ess_base.tests.common import EssCommon


class EssPayrollCommon:

    @classmethod
    def _slip(cls, employee, **vals):
        Payslip = cls.env['hr.payslip']
        base = {'name': 'Slip %s' % employee.name, 'employee_id': employee.id,
                'date_from': date(2026, 1, 1), 'date_to': date(2026, 1, 31)}
        # Enterprise hr_payroll 19 links the payslip to the employee version (assumed field name)
        if 'version_id' in Payslip._fields:
            base['version_id'] = employee.sudo().version_id.id
        return Payslip.create({**base, **vals})


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssPayrollBridge(EssCommon, EssPayrollCommon):

    def _adj(self, amount, adj_type='deduction', day=15):
        return self.env['bs.hr.payroll.adjustment'].create({
            'employee_id': self.emp_employee.id, 'name': 'Test %s' % amount, 'amount': amount,
            'category': 'other', 'adj_type': adj_type, 'date': date(2026, 1, day)})

    def test_inputs_injected_once_and_applied(self):
        ded, add = self._adj(300), self._adj(100, 'addition')
        later = self._adj(50)
        later.date = date(2026, 2, 10)
        slip = self._slip(self.emp_employee)
        slip.compute_sheet()
        inputs = slip.input_line_ids.filtered('ess_adjustment_id')
        self.assertEqual(inputs.ess_adjustment_id, ded | add)
        self.assertEqual(ded.payslip_id, slip)
        self.assertFalse(later.payslip_id)
        # recompute: no duplicates
        slip.write({'state': 'draft'})
        slip.compute_sheet()
        self.assertEqual(len(slip.input_line_ids.filtered('ess_adjustment_id')), 2)
        # amount changed -> input replaced
        ded.amount = 250
        slip.write({'state': 'draft'})
        slip.compute_sheet()
        self.assertEqual(slip.input_line_ids.filtered(lambda i: i.ess_adjustment_id == ded).amount, 250)
        # deduction / addition input types
        self.assertEqual(ded.payslip_input_id.input_type_id,
                         self.env['bs.hr.payroll.compat']._input_type('deduction'))
        slip.action_payslip_done()
        self.assertEqual((ded | add).mapped('state'), ['applied', 'applied'])
        # another payslip of the same period does not take them again
        slip2 = self._slip(self.emp_employee)
        slip2._ess_sync_adjustment_inputs()
        self.assertFalse(slip2.input_line_ids)

    def test_cancel_and_reset(self):
        ded = self._adj(300)
        slip = self._slip(self.emp_employee)
        slip.compute_sheet()
        slip.action_payslip_done()
        self.assertEqual(ded.state, 'applied')
        slip.write({'state': 'draft'})
        self.assertEqual(ded.state, 'pending')
        self.assertEqual(ded.payslip_id, slip)
        slip.write({'state': 'cancel'} if 'cancel' in self.env['bs.hr.payroll.compat']._state_keys()
                   else {'state': 'draft'})
        self.assertEqual(ded.state, 'pending')
        self.assertFalse(ded.payslip_id)
        # cancelled adjustment in a draft payslip: input removed
        ded2 = self._adj(40)
        slip3 = self._slip(self.emp_employee)
        slip3._ess_sync_adjustment_inputs()
        self.assertTrue(ded2.payslip_input_id)
        ded2.action_cancel()
        self.assertFalse(slip3.input_line_ids.filtered(lambda i: i.ess_adjustment_id == ded2))

    def test_objection_creates_adjustment(self):
        slip = self._slip(self.emp_employee)
        slip.compute_sheet()
        slip.action_payslip_done()
        payroll_user = new_test_user(self.env, login='ess_payroll',
                                     groups='bs_hr_ess_base.group_ess_payroll_officer')
        objection = self.env['bs.hr.payslip.objection'].create({
            'employee_id': self.emp_employee.id, 'payslip_id': slip.id,
            'line_id': slip.line_ids[:1].id, 'claimed_amount': 200, 'reason': 'Absent day wrongly deducted'})
        objection.action_submit()
        self.assertEqual(objection.current_approver_ids, self.user_manager)
        objection.with_user(self.user_manager).sudo().action_ess_approve('attendance ok')
        self.assertIn(payroll_user, objection.current_approver_ids)
        objection.with_user(payroll_user).action_ess_approve(
            values={'resolution_amount': 150, 'resolution_type': 'addition', 'state': 'draft'})
        self.assertEqual(objection.state, 'done')
        adj = objection.adjustment_id
        self.assertEqual((adj.amount, adj.adj_type, adj.category), (150, 'addition', 'objection'))
        self.assertEqual(adj.source_ref, objection.display_name)
        # it reaches the next payslip
        next_slip = self._slip(self.emp_employee, date_from=date(2026, 11, 1), date_to=date(2026, 12, 31))
        next_slip._ess_sync_adjustment_inputs()
        self.assertEqual(next_slip.input_line_ids.ess_adjustment_id, adj)

    def test_publish_run_and_dashboard(self):
        run = self.env['hr.payslip.run'].create({'name': 'Jan'})
        slip = self._slip(self.emp_employee, payslip_run_id=run.id)
        self.assertFalse(slip._ess_portal_visible())
        run.action_ess_publish()
        self.assertTrue(slip.ess_published)
        self.assertTrue(slip._ess_portal_visible())
        self.assertTrue(self.env['bs.hr.notification'].search_count([
            ('employee_id', '=', self.emp_employee.id), ('res_model', '=', 'hr.payslip')]))
        slip.compute_sheet()
        slip.action_payslip_done()
        self.assertFalse(slip.ess_published)
        card = [c for c in self.emp_employee._ess_dashboard_cards() if c['key'] == 'payslip'][0]
        self.assertEqual(card['url'], '/my/hr/payslip/%s' % slip.id)


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssPayrollPortal(HttpCase, EssPayrollCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_a = new_test_user(cls.env, login='ess_pay_a', password='ess_pay_a', groups='base.group_portal')
        cls.user_b = new_test_user(cls.env, login='ess_pay_b', password='ess_pay_b', groups='base.group_portal')
        cls.emp_a = cls.env['hr.employee'].create({'name': 'Pay A', 'user_id': cls.user_a.id, 'wage': 5000})
        cls.emp_b = cls.env['hr.employee'].create({'name': 'Pay B', 'user_id': cls.user_b.id, 'wage': 5000})
        cls.slip_a = cls._slip(cls.emp_a)
        cls.slip_a.compute_sheet()
        cls.slip_a.action_payslip_done()
        cls.draft_a = cls._slip(cls.emp_a, date_from=date(2026, 2, 1), date_to=date(2026, 2, 28))
        cls.slip_b = cls._slip(cls.emp_b)
        cls.slip_b.compute_sheet()
        cls.slip_b.action_payslip_done()

    def csrf_token(self):
        return self.opener.get(self.base_url() + '/my/hr').text.split('csrf_token: "')[1].split('"')[0]

    def test_portal_pages_and_security(self):
        self.authenticate('ess_pay_a', 'ess_pay_a')
        for url in ('/my/hr', '/my/hr/payslips', '/my/hr/payslip/%s' % self.slip_a.id,
                    '/my/hr/payslip/%s/objection' % self.slip_a.id):
            self.assertEqual(self.url_open(url).status_code, 200, url)
        # unpublished draft and other employee's payslip: 404
        for url in ('/my/hr/payslip/%s' % self.draft_a.id, '/my/hr/payslip/%s' % self.slip_b.id,
                    '/my/hr/payslip/%s/pdf' % self.slip_b.id, '/my/hr/payslip/%s/objection' % self.slip_b.id):
            self.assertEqual(self.url_open(url).status_code, 404, url)
        self.draft_a.ess_published = True
        res = self.url_open('/my/hr/payslip/%s' % self.draft_a.id)
        self.assertEqual(res.status_code, 200)
        self.assertIn('Draft for review', res.text)

    def test_portal_objection(self):
        self.authenticate('ess_pay_a', 'ess_pay_a')
        line = self.slip_a.line_ids[:1]
        res = self.url_open('/my/hr/payslip/%s/objection' % self.slip_a.id, data={
            'csrf_token': self.csrf_token(), 'line_id': line.id, 'claimed_amount': '120',
            'reason': 'Wrong deduction'})
        self.assertEqual(res.status_code, 200)
        objection = self.env['bs.hr.payslip.objection'].search([('payslip_id', '=', self.slip_a.id)])
        self.assertEqual(len(objection), 1)
        self.assertEqual(objection.line_id, line)
        self.assertTrue(objection.portal_source)
        # a line of another payslip is refused
        res = self.url_open('/my/hr/payslip/%s/objection' % self.slip_a.id, data={
            'csrf_token': self.csrf_token(), 'line_id': self.slip_b.line_ids[:1].id, 'reason': 'x'})
        self.assertEqual(res.status_code, 404)
