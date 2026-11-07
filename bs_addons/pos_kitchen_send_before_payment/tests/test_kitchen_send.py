from odoo import Command
from odoo.tests import tagged
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon


@tagged('post_install', '-at_install')
class TestKitchenSendBeforePayment(TestPointOfSaleHttpCommon):

    def test_send_to_kitchen_before_payment(self):
        self.printer.write({
            'product_categories_ids': [Command.set(self.pos_cat_desk_test.ids)],
        })
        self.main_pos_config.write({
            'module_pos_restaurant': False,
            'is_order_printer': True,
            'printer_ids': [Command.set(self.printer.ids)],
            'kitchen_send_before_payment': True,
        })
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour('pos_kitchen_send_before_payment_tour', login='pos_user')

        order = self.env['pos.order'].search(
            [('session_id', '=', self.main_pos_config.current_session_id.id)])
        self.assertEqual(len(order), 1)
        self.assertEqual(order.state, 'paid')

    def test_button_hidden_when_disabled(self):
        self.printer.write({
            'product_categories_ids': [Command.set(self.pos_cat_desk_test.ids)],
        })
        self.main_pos_config.write({
            'is_order_printer': True,
            'printer_ids': [Command.set(self.printer.ids)],
            'kitchen_send_before_payment': False,
        })
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour('pos_kitchen_send_disabled_tour', login='pos_user')

    def test_no_printer_shows_explanation(self):
        self.main_pos_config.write({
            'is_order_printer': False,
            'kitchen_send_before_payment': True,
        })
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour('pos_kitchen_send_no_printer_tour', login='pos_user')
