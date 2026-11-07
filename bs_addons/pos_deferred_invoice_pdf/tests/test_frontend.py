from odoo.tests import tagged
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon


@tagged('post_install', '-at_install')
class TestDeferredInvoicePdfFrontend(TestPointOfSaleHttpCommon):
    def _prepare_deferred_invoice_sale(self, automatic_printing):
        self.env.ref('account.ir_cron_account_move_send').active = True
        self.main_pos_config.write({
            'always_to_invoice': True,
            'defer_invoice_pdf_generation': True,
            'iface_print_auto': automatic_printing,
            'iface_print_skip_screen': automatic_printing,
            'payment_method_ids': [(4, self.bank_payment_method.id)],
        })
        self.env['product.product'].create({
            'name': 'Product Test',
            'available_in_pos': True,
            'list_price': 20,
            'taxes_id': False,
        })

    def _assert_invoice_queued_then_generated(self):
        order = self.env['pos.order'].search([
            ('config_id', '=', self.main_pos_config.id),
            ('partner_id', '=', self.partner_test_1.id),
        ], order='id desc', limit=1)
        self.assertTrue(order)
        self.assertEqual(order.state, 'done')
        self.assertTrue(order.to_invoice)
        self.assertEqual(order.account_move.state, 'posted')
        self.assertTrue(order.account_move.sending_data)
        self.assertFalse(order.account_move.invoice_pdf_report_id)

        with self.enter_registry_test_mode():
            self.env.ref('account.ir_cron_account_move_send').method_direct_trigger()

        order.account_move.invalidate_recordset()
        self.assertTrue(order.account_move.invoice_pdf_report_id)
        self.assertFalse(order.account_move.sending_data)

    def test_payment_reaches_receipt_before_invoice_pdf_exists(self):
        self._prepare_deferred_invoice_sale(automatic_printing=False)
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour('deferred_invoice_payment_tour')
        self._assert_invoice_queued_then_generated()

    def test_automatic_printing_reaches_next_order_before_pdf_exists(self):
        self._prepare_deferred_invoice_sale(automatic_printing=True)
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour('deferred_invoice_payment_auto_print_tour')
        self._assert_invoice_queued_then_generated()
