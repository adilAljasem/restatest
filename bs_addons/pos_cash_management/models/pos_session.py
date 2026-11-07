# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import float_is_zero


class PosSession(models.Model):
    _inherit = 'pos.session'

    pcm_transferred_amount = fields.Monetary(
        string="Transferred to Bank", readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _pcm_rounding(self):
        self.ensure_one()
        return (self.currency_id or self.company_id.currency_id).rounding

    def _pcm_has_cash_activity(self):
        """Whether real cash moved during this session."""
        self.ensure_one()
        rnd = self._pcm_rounding()
        if not float_is_zero(self.cash_register_balance_start, precision_rounding=rnd):
            return True
        cash_pm = self.payment_method_ids.filtered('is_cash_count')[:1]
        if cash_pm:
            domain = Domain.AND([
                self._get_captured_payments_domain(),
                [('payment_method_id', '=', cash_pm.id)],
            ])
            groups = self.env['pos.payment']._read_group(domain, aggregates=['amount:sum'])
            total = (groups[0][0] if groups else 0.0) or 0.0
            if not float_is_zero(total, precision_rounding=rnd):
                return True
        return not float_is_zero(self.cash_register_balance_end, precision_rounding=rnd)

    def _pcm_cash_count_missing(self, counted_cash):
        """The mandatory cash count rule, shared by the server and the client."""
        self.ensure_one()
        if not self.config_id.pcm_require_count:
            return False
        if not float_is_zero(counted_cash, precision_rounding=self._pcm_rounding()):
            return False
        return self._pcm_has_cash_activity()

    # ------------------------------------------------------------------
    # Closing control data
    # ------------------------------------------------------------------
    def get_closing_control_data(self):
        data = super().get_closing_control_data()

        # Lets the interface raise the mandatory count error before the
        # Payments Difference dialog, without disclosing any amount.
        data['pcm_has_cash_activity'] = (
            self.config_id.pcm_require_count and self._pcm_has_cash_activity()
        )

        if not self.config_id.pcm_blind_count:
            return data

        cash = data.get('default_cash_details')
        if cash:
            cash.update({
                'amount': 0.0,
                'opening': 0.0,
                'payment_amount': 0.0,
                'moves': [],
            })
            # Added by pos_hr, would leak the expected amounts back.
            if 'amount_per_employee' in cash:
                cash['amount_per_employee'] = []
            if 'moves_per_employee' in cash:
                cash['moves_per_employee'] = []
        orders = data.get('orders_details')
        if orders:
            orders['amount'] = 0.0
        return data

    # ------------------------------------------------------------------
    # Mandatory cash count
    # ------------------------------------------------------------------
    def post_closing_cash_details(self, counted_cash):
        self.ensure_one()
        if self._pcm_cash_count_missing(counted_cash):
            raise UserError(_(
                "Please enter the actual Cash Count before closing the session."
            ))
        return super().post_closing_cash_details(counted_cash)

    # ------------------------------------------------------------------
    # Cash transfer to the bank
    # ------------------------------------------------------------------
    def _pcm_create_transfer_line(self, amount):
        """Register ``amount`` as a cash out (or cash in when negative).

        ``try_cash_in_out`` requires cash move rights that a plain cashier
        does not have, so the statement line is created directly with the
        very same values the standard method would use.
        """
        self.ensure_one()
        out = amount > 0
        sign = -1 if out else 1
        reason = self.config_id.pcm_transfer_reason or _("Cash transfer to bank")
        extras = {'translatedType': _("Cash Out") if out else _("Cash In")}
        if self.employee_id:
            extras['employee_id'] = self.employee_id.id
        vals = self._prepare_account_bank_statement_line_vals(
            self, sign, abs(amount), reason, False, extras)
        self.env['account.bank.statement.line'].sudo().with_context(
            no_retrieve_partner=True).create(vals)

    def _pcm_transfer_counted_cash(self):
        """Hand the counted cash over to the bank.

        Runs once the session is closed, so the closing report keeps the
        amounts the cashier actually counted: while the session is closed,
        ``cash_register_balance_end`` is computed from ``cash_real_transaction``
        rather than from the live statement lines, so this cash out changes
        neither the expected nor the counted nor the difference amount.
        """
        self.ensure_one()
        counted = self.cash_register_balance_end_real
        delta = counted - self.pcm_transferred_amount
        if float_is_zero(delta, precision_rounding=self._pcm_rounding()):
            return
        self._pcm_create_transfer_line(delta)
        self.pcm_transferred_amount = counted

    def _validate_session(self, balancing_account=False, amount_to_balance=0,
                          bank_payment_method_diffs=None):
        result = super()._validate_session(
            balancing_account, amount_to_balance, bank_payment_method_diffs)
        # `True` means the session really closed; anything else is the
        # imbalance wizard, after a rollback.
        if result is True and self.config_id.pcm_auto_transfer and self.cash_journal_id:
            self.sudo()._pcm_transfer_counted_cash()
        return result

    # ------------------------------------------------------------------
    # Cashier partner on the cash difference
    # ------------------------------------------------------------------
    def _pcm_difference_partner(self):
        self.ensure_one()
        employee = self.employee_id or self.user_id.employee_id
        if not employee:
            return self.env['res.partner']
        return employee.sudo().work_contact_id

    def _pcm_difference_account(self, amount):
        self.ensure_one()
        if not amount:
            return self.env['account.account']
        return (
            self.cash_journal_id.loss_account_id
            if amount < 0.0
            else self.cash_journal_id.profit_account_id
        )

    def _pcm_apply_difference_partner(self, statement_lines, amount):
        self.ensure_one()
        partner = self._pcm_difference_partner()
        account = self._pcm_difference_account(amount)
        if not partner or not account or not statement_lines:
            return

        cash_account = self.cash_journal_id.default_account_id
        target_lines = statement_lines.move_id.line_ids.filtered(
            lambda line: line.account_id == account and line.account_id != cash_account
        )
        if target_lines:
            target_lines.sudo().with_company(
                self.company_id or self.env.company
            ).with_context(skip_invoice_sync=True).write({'partner_id': partner.id})

    def _get_diff_vals(self, payment_method_id, diff_amount, outstanding_account=False):
        values = super()._get_diff_vals(payment_method_id, diff_amount, outstanding_account)
        if (
            values
            and self.config_id.pcm_difference_partner
            and self.env['pos.payment.method'].browse(payment_method_id).is_cash_count
        ):
            partner = self._pcm_difference_partner()
            if partner:
                values[1]['partner_id'] = partner.id
        return values

    def _post_statement_difference(self, amount):
        if not amount or not self.config_id.pcm_difference_partner:
            return super()._post_statement_difference(amount)

        StatementLine = self.env['account.bank.statement.line'].sudo()
        lines_before = self.sudo().statement_line_ids.ids
        result = super()._post_statement_difference(amount)
        new_lines = StatementLine.search([
            ('pos_session_id', '=', self.id),
            ('id', 'not in', lines_before),
        ])
        self._pcm_apply_difference_partner(new_lines, amount)
        return result
