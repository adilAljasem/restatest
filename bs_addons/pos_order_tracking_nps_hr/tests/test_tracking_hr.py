from odoo import Command
from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestPosOrderTrackingHr(TestPoSCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.config.write({'tracking_enabled': True})
        cls.emp_a, cls.emp_b = cls.env['hr.employee'].sudo().create([{'name': 'Cashier A'}, {'name': 'Cashier B'}])

    def test_filter_by_employee(self):
        self.open_new_session()
        order = next(iter(self._create_orders([{'pos_order_lines_ui_args': [(self.product, 1)]}]).values()))
        order.sudo().employee_id = self.emp_a
        self.config.sudo().write({
            'module_pos_hr': True,
            'tracking_employee_ids': [Command.set([self.emp_b.id])],
            # a user filter must be ignored in employee mode
            'tracking_user_ids': [Command.set([self.env.ref('base.user_root').id])],
        })
        self.assertFalse(order._tracking_is_eligible())
        self.config.sudo().tracking_employee_ids = [Command.set([self.emp_a.id])]
        self.assertTrue(order._tracking_is_eligible())
        self.config.sudo().tracking_employee_ids = [Command.clear()]
        self.assertTrue(order._tracking_is_eligible())
