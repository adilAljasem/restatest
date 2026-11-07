# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.common import CommonPosTest


@tagged('post_install', '-at_install')
class TestPosCashManagement(CommonPosTest):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.pos_config_usd
        cls.cashier_user = cls.env['res.users'].create({
            'name': 'PoS Cashier',
            'login': 'pcm_cashier',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('point_of_sale.group_pos_user').id,
            ])],
        })

    def _open_session(self, opening_cash=100.0):
        self.config.open_ui()
        session = self.config.current_session_id
        session.set_opening_control(opening_cash, None)
        return session

    # ------------------------------------------------------------------
    # Mandatory Cash Count
    # ------------------------------------------------------------------
    def test_require_count_blocks_cashier(self):
        self.config.pcm_require_count = True
        session = self._open_session(100.0)
        with self.assertRaises(UserError):
            session.with_user(self.cashier_user).post_closing_cash_details(0.0)

    def test_require_count_blocks_manager_too(self):
        """The rule applies to every user, managers included."""
        self.config.pcm_require_count = True
        session = self._open_session(100.0)
        with self.assertRaises(UserError):
            session.post_closing_cash_details(0.0)
        self.assertEqual(session.state, 'opened')

    def test_require_count_allows_empty_drawer(self):
        """A session where no cash ever moved can still be closed on zero."""
        self.config.pcm_require_count = True
        session = self._open_session(0.0)
        session.with_user(self.cashier_user).post_closing_cash_details(0.0)
        self.assertEqual(session.cash_register_balance_end_real, 0.0)

    def test_require_count_passes_once_counted(self):
        self.config.pcm_require_count = True
        session = self._open_session(100.0)
        session.post_closing_cash_details(90.0)
        self.assertEqual(session.cash_register_balance_end_real, 90.0)
        self.assertEqual(session.cash_register_difference, -10.0)

    def test_require_count_flag_reaches_the_interface(self):
        """The popup needs the flag to raise the error before the difference dialog."""
        self.config.pcm_require_count = True
        session = self._open_session(100.0)
        self.assertTrue(session.get_closing_control_data()['pcm_has_cash_activity'])

        self.config.pcm_require_count = False
        self.assertFalse(session.get_closing_control_data()['pcm_has_cash_activity'])

    def test_require_count_flag_is_false_on_empty_drawer(self):
        self.config.pcm_require_count = True
        session = self._open_session(0.0)
        self.assertFalse(session.get_closing_control_data()['pcm_has_cash_activity'])

    def test_no_option_keeps_standard_behaviour(self):
        self.config.write({
            'pcm_require_count': False,
            'pcm_auto_transfer': False,
        })
        session = self._open_session(100.0)
        session.post_closing_cash_details(90.0)
        self.assertEqual(session.cash_register_balance_end_real, 90.0)
        self.assertEqual(session.cash_register_difference, -10.0)

    # ------------------------------------------------------------------
    # Enforce Maximum Difference on Server — removed
    # ------------------------------------------------------------------
    def test_enforce_max_diff_is_gone(self):
        self.assertNotIn('pcm_enforce_max_diff', self.env['pos.config']._fields)
        self.assertNotIn(
            'pos_pcm_enforce_max_diff', self.env['res.config.settings']._fields)

    def test_standard_maximum_difference_is_untouched(self):
        """Only Odoo's own browser-side check remains."""
        self.config.write({
            'set_maximum_difference': True,
            'amount_authorized_diff': 5.0,
        })
        session = self._open_session(100.0)
        session.with_user(self.cashier_user).post_closing_cash_details(80.0)
        self.assertEqual(session.cash_register_balance_end_real, 80.0)
        self.assertEqual(
            session.get_closing_control_data()['amount_authorized_diff'], 5.0)

    # ------------------------------------------------------------------
    # Transfer Cash to Bank on Closing
    # ------------------------------------------------------------------
    def test_transfer_keeps_closing_report_intact(self):
        """Expected 203.55 / counted 150 / difference -53.55, then a 150 cash out."""
        self.config.write({
            'pcm_auto_transfer': True,
            'pcm_transfer_reason': 'Cash to bank',
        })
        session = self._open_session(203.55)
        self.assertEqual(session.cash_register_balance_end, 203.55)

        session.post_closing_cash_details(150.0)
        # cash_register_difference does not declare a dependency on
        # cash_register_balance_end_real; in the real flow each step is its
        # own request, so mimic that here.
        session.invalidate_recordset()

        # The closing report is read before and after validation.
        self.assertEqual(session.cash_register_balance_end, 203.55)
        self.assertEqual(session.cash_register_balance_end_real, 150.0)
        self.assertEqual(session.cash_register_difference, -53.55)

        session.close_session_from_ui()
        session.invalidate_recordset()
        self.assertEqual(session.state, 'closed')

        # The report still shows what the cashier counted.
        self.assertEqual(session.cash_register_balance_end, 203.55)
        self.assertEqual(session.cash_register_balance_end_real, 150.0)
        self.assertEqual(session.cash_register_difference, -53.55)

        # And the cash out was booked for the full counted amount.
        transfer = session.sudo().statement_line_ids.filtered(
            lambda line: 'Cash to bank' in (line.payment_ref or ''))
        self.assertEqual(len(transfer), 1)
        self.assertEqual(transfer.amount, -150.0)
        self.assertEqual(session.pcm_transferred_amount, 150.0)

        # Drawer emptied: opening + sales - loss - transfer == 0
        self.assertEqual(
            sum(session.sudo().statement_line_ids.mapped('amount')) + 203.55, 0.0)

    def test_transfer_with_real_cash_sales(self):
        """The acceptance scenario: 203.55 of cash sales, 150 counted."""
        self.config.write({
            'pcm_auto_transfer': True,
            'pcm_transfer_reason': 'Cash to bank',
        })
        self.config.open_ui()
        session = self.config.current_session_id
        session.set_opening_control(0.0, None)
        self.create_backend_pos_order({
            'pos_config': self.config,
            'line_data': [{
                'product_id': self.ten_dollars_no_tax.product_variant_id.id,
                'price_unit': 203.55,
                'tax_ids': [(6, 0, [])],
            }],
            'payment_data': [
                {'payment_method_id': self.cash_payment_method.id, 'amount': 203.55},
            ],
        })
        session.invalidate_recordset()
        self.assertEqual(session.cash_register_balance_end, 203.55)

        session.post_closing_cash_details(150.0)
        session.invalidate_recordset()
        self.assertEqual(session.cash_register_difference, -53.55)

        session.close_session_from_ui()
        session.invalidate_recordset()
        self.assertEqual(session.state, 'closed')
        self.assertEqual(session.cash_register_balance_end, 203.55)
        self.assertEqual(session.cash_register_balance_end_real, 150.0)
        self.assertEqual(session.cash_register_difference, -53.55)

        transfer = session.sudo().statement_line_ids.filtered(
            lambda line: 'Cash to bank' in (line.payment_ref or ''))
        self.assertEqual(transfer.amount, -150.0)

    def test_transfer_closing_message_uses_counted_amount(self):
        self.config.pcm_auto_transfer = True
        session = self._open_session(203.55)
        session.post_closing_cash_details(150.0)
        session.invalidate_recordset()
        session.update_closing_control_state_session(None)
        body = str(session.message_ids.sorted('id')[-1].body)
        self.assertIn('Closing', body)
        self.assertIn('203.55', body, "Closing expected must stay 203.55")
        self.assertIn('150.00', body, "Closing counted must stay 150.00")
        self.assertIn('53.55', body, "Closing difference must stay -53.55")

    def test_transfer_books_the_expected_journal_items(self):
        self.config.pcm_auto_transfer = True
        session = self._open_session(203.55)
        session.post_closing_cash_details(150.0)
        session.close_session_from_ui()

        cash_account = session.cash_journal_id.default_account_id
        loss_account = session.cash_journal_id.loss_account_id
        lines = session.sudo().statement_line_ids.move_id.line_ids
        self.assertEqual(
            round(sum(lines.filtered(lambda l: l.account_id == loss_account).mapped('balance')), 2),
            53.55, "The real shortage is booked to the loss account")
        self.assertEqual(
            round(sum(lines.filtered(lambda l: l.account_id == cash_account).mapped('balance')), 2),
            -203.55, "The drawer is emptied by the shortage plus the transfer")

    def test_no_transfer_when_nothing_counted(self):
        self.config.pcm_auto_transfer = True
        session = self._open_session(100.0)
        session.post_closing_cash_details(0.0)
        session.close_session_from_ui()
        self.assertEqual(session.pcm_transferred_amount, 0.0)
        self.assertFalse(session.sudo().statement_line_ids.filtered(
            lambda line: 'transfer' in (line.payment_ref or '').lower()))

    def test_no_transfer_when_option_is_off(self):
        self.config.pcm_auto_transfer = False
        session = self._open_session(100.0)
        session.post_closing_cash_details(90.0)
        session.close_session_from_ui()
        self.assertEqual(session.pcm_transferred_amount, 0.0)
        self.assertEqual(session.cash_register_balance_end_real, 90.0)

    # ------------------------------------------------------------------
    # Blind Cash Count
    # ------------------------------------------------------------------
    def test_blind_count_hides_amounts(self):
        session = self._open_session(100.0)
        data = session.get_closing_control_data()
        self.assertEqual(data['default_cash_details']['amount'], 100.0)

        self.config.pcm_blind_count = True
        data = session.get_closing_control_data()
        cash = data['default_cash_details']
        self.assertEqual(cash['amount'], 0.0)
        self.assertEqual(cash['opening'], 0.0)
        self.assertEqual(cash['payment_amount'], 0.0)
        self.assertEqual(cash['moves'], [])
        self.assertEqual(cash['amount_per_employee'], [])
        self.assertEqual(cash['moves_per_employee'], [])
        self.assertEqual(data['orders_details']['amount'], 0.0)
        # The payment method id must survive, the interface keys on it.
        self.assertTrue(cash['id'])

    # ------------------------------------------------------------------
    # Cashier Partner on Cash Difference
    # ------------------------------------------------------------------
    def test_difference_partner(self):
        contact = self.env['res.partner'].create({'name': 'Cashier Contact'})
        employee = self.env['hr.employee'].sudo().create({
            'name': 'Cashier Employee',
            'work_contact_id': contact.id,
        })
        self.config.pcm_difference_partner = True
        session = self._open_session(100.0)
        session.employee_id = employee

        session._post_statement_difference(10.0)

        line = self.env['account.bank.statement.line'].search(
            [('pos_session_id', '=', session.id)], order='id desc', limit=1)
        profit_account = session.cash_journal_id.profit_account_id
        counterpart = line.move_id.line_ids.filtered(
            lambda l: l.account_id == profit_account)
        self.assertTrue(counterpart)
        self.assertEqual(counterpart.partner_id, contact)
        cash_line = line.move_id.line_ids - counterpart
        self.assertFalse(cash_line.partner_id, "Only the counterpart carries the cashier")

    def test_difference_partner_disabled(self):
        contact = self.env['res.partner'].create({'name': 'Cashier Contact 2'})
        employee = self.env['hr.employee'].sudo().create({
            'name': 'Cashier Employee 2',
            'work_contact_id': contact.id,
        })
        self.config.pcm_difference_partner = False
        session = self._open_session(100.0)
        session.employee_id = employee

        session._post_statement_difference(10.0)

        line = self.env['account.bank.statement.line'].search(
            [('pos_session_id', '=', session.id)], order='id desc', limit=1)
        self.assertFalse(line.move_id.line_ids.partner_id)
