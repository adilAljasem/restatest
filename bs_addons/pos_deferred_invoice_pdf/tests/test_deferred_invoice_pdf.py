from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged
from odoo.addons.point_of_sale.tests.common import CommonPosTest


@tagged('post_install', '-at_install')
class TestDeferredInvoicePdf(CommonPosTest):
    def _pay_invoiced_order(self):
        order, _refund = self.create_backend_pos_order({
            'pos_config': self.pos_config_usd,
            'order_data': {
                'partner_id': self.partner_adgu.id,
                'to_invoice': True,
            },
            'line_data': [{
                'product_id': self.twenty_dollars_no_tax.product_variant_id.id,
                'qty': 1,
            }],
            'payment_data': [{
                'payment_method_id': self.cash_payment_method.id,
                'amount': 20,
            }],
        })
        return order

    def test_pdf_is_generated_after_payment_by_account_cron(self):
        self.pos_config_usd.defer_invoice_pdf_generation = True
        self.partner_adgu.write({
            'email': 'deferred-pdf-customer@example.com',
            'invoice_sending_method': 'email',
        })
        cron = self.env.ref('account.ir_cron_account_move_send')
        cron.active = True

        order = self._pay_invoiced_order()
        invoice = order.account_move

        self.assertEqual(invoice.state, 'posted')
        self.assertTrue(invoice.payment_state in ('paid', 'in_payment'))
        self.assertFalse(invoice.invoice_pdf_report_id)
        self.assertTrue(invoice.sending_data)
        self.assertTrue(invoice.pos_deferred_pdf_requested)
        self.assertEqual(self.env['account.move.send']._get_default_sending_methods(invoice), set())
        with self.assertRaisesRegex(UserError, 'prepared'):
            invoice.action_invoice_download_pdf()

        with patch.object(type(self.env['account.move.send']), '_send_mail') as send_mail:
            with self.enter_registry_test_mode():
                cron.method_direct_trigger()

        send_mail.assert_not_called()

        self.assertTrue(invoice.invoice_pdf_report_id)
        self.assertFalse(invoice.sending_data)
        self.assertEqual(self.env['account.move.send']._get_default_sending_methods(invoice), {'email'})
        self.assertEqual(invoice.action_invoice_download_pdf()['type'], 'ir.actions.act_url')
        document = invoice._get_invoice_legal_documents('pdf', allow_fallback=True)
        self.assertEqual(document['filename'], invoice.invoice_pdf_report_id.name)
        self.assertEqual(document['content'], invoice.invoice_pdf_report_id.raw)

    def test_pos_session_loads_the_deferred_pdf_setting(self):
        self.pos_config_usd.defer_invoice_pdf_generation = True

        loaded = self.pos_config_usd._load_pos_data_read(self.pos_config_usd, self.pos_config_usd)

        self.assertTrue(loaded[0]['defer_invoice_pdf_generation'])

    def test_always_to_invoice_still_queues_the_pdf(self):
        self.pos_config_usd.write({
            'always_to_invoice': True,
            'defer_invoice_pdf_generation': True,
        })
        self.env.ref('account.ir_cron_account_move_send').active = True

        order, _refund = self.create_backend_pos_order({
            'pos_config': self.pos_config_usd,
            'order_data': {'partner_id': self.partner_adgu.id},
            'line_data': [{
                'product_id': self.twenty_dollars_no_tax.product_variant_id.id,
                'qty': 1,
            }],
            'payment_data': [{
                'payment_method_id': self.cash_payment_method.id,
                'amount': 20,
            }],
        })

        self.assertTrue(order.to_invoice)
        self.assertEqual(order.account_move.state, 'posted')
        self.assertTrue(order.account_move.sending_data)
        self.assertFalse(order.account_move.invoice_pdf_report_id)

    def test_pos_cashier_can_pay_while_pdf_is_deferred(self):
        self.pos_config_usd.defer_invoice_pdf_generation = True
        self.env.ref('account.ir_cron_account_move_send').active = True
        cashier = new_test_user(
            self.env,
            login='deferred_pdf_cashier',
            groups='base.group_user,point_of_sale.group_pos_user',
        )
        self.pos_config_usd.with_user(cashier).open_ui()
        order, _refund = self.create_backend_pos_order({
            'pos_config': self.pos_config_usd,
            'order_data': {
                'partner_id': self.partner_adgu.id,
                'to_invoice': True,
            },
            'line_data': [{
                'product_id': self.twenty_dollars_no_tax.product_variant_id.id,
                'qty': 1,
            }],
        })
        order.add_payment({
            'pos_order_id': order.id,
            'payment_method_id': self.cash_payment_method.id,
            'amount': 20,
        })

        order.with_user(cashier)._process_saved_order(False)

        self.assertEqual(order.account_move.state, 'posted')
        self.assertTrue(order.account_move.sending_data)
        self.assertFalse(order.account_move.invoice_pdf_report_id)
        with self.assertRaisesRegex(UserError, 'being prepared'):
            order.account_move.with_user(cashier).action_invoice_download_pdf()

        with self.enter_registry_test_mode():
            self.env.ref('account.ir_cron_account_move_send').method_direct_trigger()

        self.assertEqual(
            order.account_move.with_user(cashier).action_invoice_download_pdf()['type'],
            'ir.actions.act_url',
        )

    def test_pdf_remains_immediate_when_setting_is_off(self):
        self.pos_config_usd.defer_invoice_pdf_generation = False

        order = self._pay_invoiced_order()

        self.assertEqual(order.account_move.state, 'posted')
        self.assertTrue(order.account_move.invoice_pdf_report_id)
        self.assertFalse(order.account_move.sending_data)
        self.assertFalse(order.account_move.pos_deferred_pdf_requested)

    def test_pdf_remains_immediate_when_send_cron_is_inactive(self):
        self.pos_config_usd.defer_invoice_pdf_generation = True
        self.env.ref('account.ir_cron_account_move_send').active = False

        order = self._pay_invoiced_order()

        self.assertEqual(order.account_move.state, 'posted')
        self.assertTrue(order.account_move.invoice_pdf_report_id)
        self.assertFalse(order.account_move.sending_data)

    def test_repeating_invoice_action_keeps_one_invoice_and_one_pdf_job(self):
        self.pos_config_usd.defer_invoice_pdf_generation = True
        self.env.ref('account.ir_cron_account_move_send').active = True

        order = self._pay_invoiced_order()
        invoice = order.account_move
        queued_data = invoice.sending_data
        action = order.action_pos_order_invoice()

        self.assertEqual(action['res_id'], invoice.id)
        self.assertEqual(order.account_move, invoice)
        self.assertEqual(invoice.sending_data, queued_data)
        self.assertEqual(self.env['pos.order'].search_count([('account_move', '=', invoice.id)]), 1)

    def test_non_invoiced_order_does_not_queue_pdf(self):
        self.pos_config_usd.defer_invoice_pdf_generation = True
        order, _refund = self.create_backend_pos_order({
            'pos_config': self.pos_config_usd,
            'line_data': [{
                'product_id': self.twenty_dollars_no_tax.product_variant_id.id,
                'qty': 1,
            }],
            'payment_data': [{
                'payment_method_id': self.cash_payment_method.id,
                'amount': 20,
            }],
        })

        self.assertEqual(order.state, 'paid')
        self.assertFalse(order.to_invoice)
        self.assertFalse(order.account_move)

    def test_refund_invoice_pdf_is_queued_and_generated(self):
        self.pos_config_usd.defer_invoice_pdf_generation = True
        self.env.ref('account.ir_cron_account_move_send').active = True
        order, refund = self.create_backend_pos_order({
            'pos_config': self.pos_config_usd,
            'order_data': {
                'partner_id': self.partner_adgu.id,
                'to_invoice': True,
            },
            'line_data': [{
                'product_id': self.twenty_dollars_no_tax.product_variant_id.id,
                'qty': 1,
            }],
            'payment_data': [{
                'payment_method_id': self.cash_payment_method.id,
                'amount': 20,
            }],
            'refund_data': [{
                'payment_method_id': self.cash_payment_method.id,
            }],
        })

        self.assertEqual(order.account_move.state, 'posted')
        self.assertEqual(refund.account_move.move_type, 'out_refund')
        self.assertEqual(refund.account_move.state, 'posted')
        self.assertFalse(refund.account_move.invoice_pdf_report_id)
        self.assertTrue(refund.account_move.sending_data)

        with self.enter_registry_test_mode():
            self.env.ref('account.ir_cron_account_move_send').method_direct_trigger()

        self.assertTrue(refund.account_move.invoice_pdf_report_id)

    def test_failed_pdf_render_can_be_retried_by_cron(self):
        self.pos_config_usd.defer_invoice_pdf_generation = True
        self.env.ref('account.ir_cron_account_move_send').active = True
        invoice = self._pay_invoiced_order().account_move

        with patch.object(type(self.env['account.move.send']), '_prepare_invoice_pdf_report',
                          side_effect=RuntimeError('pdf rendering failed')):
            with self.assertRaisesRegex(RuntimeError, 'pdf rendering failed'):
                with self.env.cr.savepoint():
                    self.env['account.move']._cron_account_move_send()

        invoice.invalidate_recordset()
        self.assertEqual(invoice.state, 'posted')
        self.assertTrue(invoice.sending_data)
        self.assertFalse(invoice.invoice_pdf_report_id)

        with self.enter_registry_test_mode():
            self.env.ref('account.ir_cron_account_move_send').method_direct_trigger()

        self.assertTrue(invoice.invoice_pdf_report_id)
        self.assertFalse(invoice.sending_data)

    def test_failed_job_scheduling_rolls_back_payment_and_invoice(self):
        self.pos_config_usd.defer_invoice_pdf_generation = True
        cron = self.env.ref('account.ir_cron_account_move_send')
        cron.active = True
        posted_before = self.env['account.move'].search_count([
            ('partner_id', '=', self.partner_adgu.id),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
        ])

        with self.assertRaisesRegex(RuntimeError, 'scheduling failed'):
            with self.env.cr.savepoint():
                with patch.object(type(cron), '_trigger', side_effect=RuntimeError('scheduling failed')):
                    self._pay_invoiced_order()

        posted_after = self.env['account.move'].search_count([
            ('partner_id', '=', self.partner_adgu.id),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
        ])
        self.assertEqual(posted_after, posted_before)

    def test_failed_pdf_job_never_downloads_a_proforma_as_invoice(self):
        self.pos_config_usd.defer_invoice_pdf_generation = True
        self.env.ref('account.ir_cron_account_move_send').active = True
        invoice = self._pay_invoiced_order().account_move
        invoice.sending_data = False

        with self.assertRaisesRegex(UserError, 'could not be prepared'):
            invoice.action_invoice_download_pdf()
        with self.assertRaisesRegex(UserError, 'could not be prepared'):
            invoice._get_invoice_legal_documents('pdf', allow_fallback=True)
        with self.assertRaisesRegex(UserError, 'could not be prepared'):
            invoice._get_invoice_legal_documents_all(allow_fallback=True)

    def test_zatca_submission_stays_in_payment_request(self):
        self.pos_config_usd.defer_invoice_pdf_generation = True
        self.env.ref('account.ir_cron_account_move_send').active = True
        send_model = type(self.env['account.move.send'])
        move_model = type(self.env['account.move'])
        edi_calls = []

        def record_edi_call(moves, **kwargs):
            if moves:
                edi_calls.append((moves.ids, kwargs))

        with patch.object(send_model, '_get_all_extra_edis', return_value={
            'sa_edi': {'label': 'ZATCA', 'is_applicable': lambda move: True},
        }):
            with patch.object(move_model, 'action_process_edi_web_services', record_edi_call):
                order = self._pay_invoiced_order()
                self.assertEqual(
                    self.env['account.move.send']._get_default_extra_edis(order.account_move), set()
                )
                with self.enter_registry_test_mode():
                    self.env.ref('account.ir_cron_account_move_send').method_direct_trigger()

        self.assertEqual(edi_calls, [(order.account_move.ids, {'with_commit': False})])
        self.assertEqual(order.account_move.state, 'posted')
        self.assertTrue(order.account_move.invoice_pdf_report_id)
        self.assertFalse(order.account_move.sending_data)
