from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.bs_hr_ess_base.tests.common import EssCommon


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssCustody(EssCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_admin = cls.env.ref('base.user_admin')
        cls.uniform = cls.env['product.product'].create({
            'name': 'ESS uniform', 'type': 'consu', 'is_storable': True, 'ess_is_custody': True,
            'ess_needs_size': True, 'ess_renew_months': 6, 'standard_price': 100})
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        cls.env['stock.quant']._update_available_quantity(cls.uniform, cls.warehouse.lot_stock_id, 10)

    def _approve_all(self, record):
        while record.state == 'in_approval':
            record.with_user(self.user_admin).action_ess_approve()

    def _issue(self, qty=2):
        req = self.env['bs.hr.custody.request'].create({
            'employee_id': self.emp_employee.id, 'request_type': 'new',
            'line_ids': [(0, 0, {'product_id': self.uniform.id, 'quantity': qty, 'size': 'L'})]})
        req.action_submit()
        self._approve_all(req)
        return req

    def test_size_required(self):
        req = self.env['bs.hr.custody.request'].create({
            'employee_id': self.emp_employee.id, 'request_type': 'new',
            'line_ids': [(0, 0, {'product_id': self.uniform.id, 'quantity': 1})]})
        with self.assertRaises(ValidationError):
            req.action_submit()

    def test_issue_picking_creates_holdings(self):
        req = self._issue()
        self.assertEqual(req.state, 'approved')
        picking = req.picking_id
        self.assertTrue(picking)
        custody_location = self.env.company.ess_custody_location_id
        self.assertEqual(picking.location_dest_id, custody_location)
        self.assertFalse(self.env['bs.hr.custody.holding'].search([('employee_id', '=', self.emp_employee.id)]))
        picking.button_validate()
        self.assertEqual(picking.state, 'done')
        self.assertEqual(req.state, 'done')
        holding = self.env['bs.hr.custody.holding'].search([('employee_id', '=', self.emp_employee.id)])
        self.assertEqual(len(holding), 1)
        self.assertEqual(holding.quantity, 2)
        self.assertEqual(holding.size, 'L')
        self.assertTrue(holding.next_due_date)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.uniform, custody_location), 2)
        # employee acknowledgement
        holding.action_acknowledge()
        self.assertTrue(holding.ack_date)
        return holding

    def test_return(self):
        holding = self.test_issue_picking_creates_holdings()
        req = self.env['bs.hr.custody.request'].create({
            'employee_id': self.emp_employee.id, 'request_type': 'return',
            'line_ids': [(0, 0, {'holding_id': holding.id, 'product_id': self.uniform.id, 'quantity': 2})]})
        req.action_submit()
        self._approve_all(req)
        self.assertEqual(req.picking_id.location_id, self.env.company.ess_custody_location_id)
        req.picking_id.button_validate()
        self.assertEqual(holding.state, 'returned')

    def test_loss_creates_adjustment(self):
        holding = self.test_issue_picking_creates_holdings()
        req = self.env['bs.hr.custody.request'].create({
            'employee_id': self.emp_employee.id, 'request_type': 'loss', 'reason': 'Lost in the bus',
            'line_ids': [(0, 0, {'holding_id': holding.id, 'product_id': self.uniform.id, 'quantity': 2})]})
        req.action_submit()
        self.assertIn('deduction_amount', req._ess_portal_approval_fields())
        req.with_user(self.user_admin).action_ess_approve(values={'deduction_amount': 150, 'state': 'done'})
        self._approve_all(req)
        self.assertEqual(req.state, 'done')
        self.assertEqual(holding.state, 'lost')
        adj = req.adjustment_id
        self.assertEqual(adj.amount, 150)
        self.assertEqual(adj.category, 'custody')
        self.assertEqual(adj.employee_id, self.emp_employee)

    def test_clearance_blocked_by_custody(self):
        self.test_issue_picking_creates_holdings()
        clearance = self.env['bs.hr.clearance'].create({'employee_id': self.emp_employee.id})
        clearance.action_check()
        self.assertEqual(clearance.state, 'blocked')
        self.assertEqual(clearance.blocker_ids.category, 'custody')

    def test_renewal_cron(self):
        holding = self.test_issue_picking_creates_holdings()
        holding.date_received = holding.date_received.replace(year=holding.date_received.year - 1)
        self.env['bs.hr.custody.holding']._cron_renewal_reminder()
        self.assertTrue(holding.renewal_notified)
        self.assertTrue(self.env['bs.hr.notification'].search([
            ('employee_id', '=', self.emp_employee.id), ('res_model', '=', holding._name)]))
