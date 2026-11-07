
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class pettyCashSettlement(models.Model):
    _name = "petty.cash.settlement"
    _description = "Petty Cash Settlement"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    def _get_expense_account_domain(self):
        company = self.petty_request_id.company_id
        if not company:
            req_id = self.env.context.get('default_petty_request_id')
            if req_id:
                company = self.env['petty.cash.request'].browse(req_id).company_id
        if not company:
            company = self.env.company
        # Odoo 19: account.account.company_id was replaced by the many2many
        # account.account.company_ids (accounts can be shared across companies).
        return [
            ('account_type', '=', 'expense'),
            ('company_ids', 'in', company.id),
        ]

    name = fields.Char('Description', required=True, copy=False)
    bill_id = fields.Many2one('account.move', 'Vendor Bill', domain=[('move_type', '=', 'in_invoice'), ('state', '=', 'posted')], copy=False)
    expense_id = fields.Many2one('hr.expense', 'Expense', domain=[('state', '=', 'posted')], copy=False)
    is_bill = fields.Boolean('Is Bill', default=False, store=True)
    is_expense = fields.Boolean('Is Expense', default=False, store=True)
    is_reconciled = fields.Boolean('Is reconciled', default=False, store=True)
    petty_request_id = fields.Many2one('petty.cash.request', 'Petty Cash Request', copy=False)
    expense_account_id = fields.Many2one('account.account', 'Expense Account', domain=_get_expense_account_domain)
    product_id = fields.Many2one('product.product', 'Product', copy=False, domain=[('can_be_expensed', '=', True)])
    amount = fields.Float('Amount', required=True)
    replenished = fields.Boolean(
        'Replenished',
        default=False,
        copy=False,
        index=True,
        readonly=True,
        help='True when this settlement line has been covered by a replenishment '
             'entry (imprest cycle closed for this line).',
    )
    replenishment_move_id = fields.Many2one(
        'account.move',
        string='Replenishment Move',
        copy=False,
        ondelete='set null',
        readonly=True,
        help='Journal entry that replenished the petty cash for this settlement.',
    )

    @api.constrains('expense_account_id', 'petty_request_id')
    def _check_expense_account_company(self):
        for rec in self:
            if rec.expense_account_id and rec.petty_request_id:
                # Odoo 19: an account belongs to a set of companies
                # (account.account.company_ids) instead of a single company_id.
                # The rule is unchanged: the account must be usable by the
                # company of the petty cash request.
                account_companies = rec.expense_account_id.company_ids
                if rec.petty_request_id.company_id not in account_companies:
                    raise ValidationError(_(
                        "Expense account %(acc)s belongs to company %(acc_co)s but this petty cash request is for "
                        "%(req_co)s. Select an expense account for the same company as the request, or switch your "
                        "active company in Odoo.",
                        acc=rec.expense_account_id.display_name,
                        acc_co=', '.join(account_companies.mapped('display_name')) or _('none'),
                        req_co=rec.petty_request_id.company_id.display_name,
                    ))

    attachment_id = fields.Many2one('ir.attachment', string='Shared attachment')
    accounting_date = fields.Date(
        string='Accounting Date',
        help='Date to be used for accounting entries instead of current date',
        default=fields.Date.today
    )
    attachment_file = fields.Binary(
        string='Attachment File',
        attachment=True,
        help='Upload file attachment for this settlement line'
    )
    attachment_filename = fields.Char(string='Filename')
    attachment_status = fields.Char(compute='_compute_attachment_status', string='File Status')
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'petty_cash_settlement_attachment_rel',
        'settlement_id',
        'attachment_id',
        string='Attachments'
    )
    settlement_date_from = fields.Date(
        string='Settlement Date From',
        help='Filter settlements from this date'
    )
    settlement_date_to = fields.Date(
        string='Settlement Date To',
        help='Filter settlements to this date'
    )
    analytic_distribution = fields.Json(
        string='Analytic Distribution',
        copy=True,
        groups="analytic.group_analytic_accounting"
    )
    analytic_precision = fields.Integer(
        string='Analytic Precision',
        store=False,
        default=lambda self: self.env['decimal.precision'].precision_get("Percentage Analytic"),
        groups="analytic.group_analytic_accounting"
    )
    refuse = fields.Boolean(
        string='Refused',
        default=False,
        tracking=True,
        help='If checked, this settlement line will be ignored in all calculations and reconciliations.'
    )
    refuse_reason = fields.Text(
        string='Notes',
        tracking=True,
    )
    tax_ids = fields.Many2many(
        'account.tax',
        string="Taxes",
        domain=[('type_tax_use', '=', 'purchase')]
    )
    is_amount_includes_tax = fields.Boolean(
        string="Price Includes Tax",
        default=False,
        help="Check if the amount entered already includes taxes"
    )
    total_with_tax = fields.Float(
        string="Total with Tax",
        compute="_compute_total_with_tax",
        store=True,
        help="Total amount after tax calculation"
    )
    petty_cash_type = fields.Selection([
        ('permanent', 'Permanent Petty Cash'),
        ('temporary', 'Temporary Petty Cash')
    ], string='Petty Cash Type', required=False)
    petty_cash_checkbox = fields.Boolean(
        string="Permanent Petty Cash"
    )
    temporary_petty_cash = fields.Boolean(
        string="Temporary Petty Cash"
    )

    # ------------------------------------------------------------------
    # Task 04 - Finance Review
    # The only approval stage left on the settlement cycle. It is granted to
    # the standard accounting group allowed to create journal entries
    # (account.group_account_user). There is deliberately no project /
    # department / purchase manager approval and no dedicated petty cash
    # approval group.
    # ------------------------------------------------------------------
    # Standard accounting groups that are allowed to create journal entries.
    # No dedicated petty cash / project / department approval group is used.
    FINANCE_REVIEW_GROUPS = (
        'account.group_account_user',
        'account.group_account_manager',
    )

    def _user_can_finance_review(self):
        """True when the current user is a standard accounting user able to
        create journal entries (Task 04)."""
        user = self.env.user
        if self.env.su or user._is_admin():
            return True
        if any(user.has_group(group) for group in self.FINANCE_REVIEW_GROUPS):
            return True
        # Fall back on the effective ACL: whoever may create account.move may
        # perform the Finance Review.
        return self.env['account.move'].has_access('create')

    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('finance_review', 'Finance Review'),
            ('approved', 'Approved'),
            ('done', 'Settled'),
            ('refused', 'Refused'),
        ],
        string='Status',
        default='draft',
        required=True,
        copy=False,
        index=True,
        tracking=True,
    )
    can_finance_review = fields.Boolean(
        string='Can Finance Review',
        compute='_compute_can_finance_review',
        help='Technical field: the current user belongs to the accounting group '
             'allowed to create journal entries.',
    )

    def _compute_can_finance_review(self):
        allowed = self._user_can_finance_review()
        for rec in self:
            rec.can_finance_review = allowed

    def action_submit_finance_review(self):
        """Send draft settlement lines to Finance Review."""
        todo = self.filtered(lambda s: s.state == 'draft' and not s.refuse)
        if not todo:
            raise UserError(_("There is no draft settlement line to submit."))
        todo.write({'state': 'finance_review'})
        return True

    def action_finance_approve(self):
        """Finance Review approval - accounting users only."""
        if not self._user_can_finance_review():
            raise UserError(_(
                "Only an accounting user allowed to create journal entries can "
                "perform the Finance Review."
            ))
        todo = self.filtered(
            lambda s: s.state in ('draft', 'finance_review') and not s.refuse
        )
        if not todo:
            raise UserError(_("There is no settlement line waiting for Finance Review."))
        todo.write({'state': 'approved'})
        for rec in todo:
            rec.message_post(
                body=_("Finance Review approved by %s.", self.env.user.display_name)
            )
        return True

    def action_reset_to_draft(self):
        self.filtered(lambda s: s.state in ('finance_review', 'approved')).write(
            {'state': 'draft'}
        )
        return True

    def _ensure_finance_reviewed(self):
        """Guarantee every line about to be posted has passed the Finance Review.

        The 'Reconcile' / 'Confirm' buttons are already restricted to
        account.group_account_user, so an accountant confirming a settlement IS
        the finance review: lines still in draft / finance_review are approved
        on the fly. This keeps the existing settlement flow identical for
        accountants while making the stage explicit and blocking anyone else.
        """
        pending = self.filtered(lambda s: s.state in ('draft', 'finance_review'))
        if not pending:
            return True
        if not self._user_can_finance_review():
            raise UserError(_(
                "These settlement lines still need the Finance Review: %(lines)s.\n"
                "Only an accounting user allowed to create journal entries can "
                "approve them.",
                lines=', '.join(pending.mapped('name')),
            ))
        pending.write({'state': 'approved'})
        return True

    @api.onchange('refuse')
    def _onchange_refuse_state(self):
        for rec in self:
            if rec.refuse and rec.state != 'done':
                rec.state = 'refused'
            elif not rec.refuse and rec.state == 'refused':
                rec.state = 'draft'

    def write(self, vals):
        res = super().write(vals)
        if 'refuse' in vals and 'state' not in vals:
            to_refuse = self.filtered(lambda s: s.refuse and s.state != 'done')
            to_reopen = self.filtered(lambda s: not s.refuse and s.state == 'refused')
            if to_refuse:
                super(pettyCashSettlement, to_refuse).write({'state': 'refused'})
            if to_reopen:
                super(pettyCashSettlement, to_reopen).write({'state': 'draft'})
        return res

    @api.depends('amount', 'tax_ids', 'is_amount_includes_tax')
    def _compute_total_with_tax(self):
        for rec in self:
            if not rec.tax_ids or not rec.amount:
                rec.total_with_tax = rec.amount
                continue

            company = rec.petty_request_id.company_id or rec.env.company
            currency = company.currency_id
            partner = rec.petty_request_id.requester_partner_id

            taxes = rec.tax_ids.with_context(
                force_price_include=rec.is_amount_includes_tax,
            )
            tax_result = taxes.compute_all(
                price_unit=rec.amount,
                currency=currency,
                quantity=1.0,
                product=rec.product_id,
                partner=partner,
                is_refund=False,
                handle_price_include=True,
            )
            rec.total_with_tax = tax_result['total_included']
    def action_filter_settlements_by_date(self):
        """Filter settlements by date range"""
        self.ensure_one()
        domain = [('id', 'in', self.ids)]
        if self.settlement_date_from:
            domain.append(('accounting_date', '>=', self.settlement_date_from))
        if self.settlement_date_to:
            domain.append(('accounting_date', '<=', self.settlement_date_to))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Filtered Settlements'),
            'res_model': 'petty.cash.settlement',
            'view_mode': 'list,form',
            'domain': domain,
            'target': 'current',
        }

    def action_import_excel_settlements(self):
        """Open wizard to upload Excel file for importing settlements"""
        return {
            'name': _('Import Settlements from Excel'),
            'type': 'ir.actions.act_window',
            'res_model': 'petty.cash.import.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_petty_request_id': self.petty_request_id.id,
            }
        }
    @api.depends('attachment_file')
    def _compute_attachment_status(self):
        for rec in self:
            if rec.attachment_file:
                rec.attachment_status = _('File Uploaded')
            else:
                rec.attachment_status = _('Click to Upload')

    @api.onchange('bill_id')
    def _onchange_bill_id(self):
        if self.bill_id:
            if self.bill_id.payment_state not in ('not_paid', 'partial'):
                self.bill_id = False
                return {'warning':
                    {
                        'title': _("Warning"),
                        'message': _('This bill is already reconciled or paid')
                    }
                }
            expense_line = self.bill_id.invoice_line_ids.filtered(
                lambda l: l.account_id and l.account_id.account_type != 'liability_payable'
            )[:1]
            self.expense_account_id = expense_line.account_id
            self.amount = self.bill_id.amount_residual
            self.is_bill = True
            self.name = _('Settlement from vendor bill by No: %(name)s', name=self.bill_id.name)
        else:
            self.amount = False
            self.is_bill = False
            self.name = False

    @api.onchange('expense_id')
    def _onchange_expense_id(self):
        if self.expense_id:
            if self.expense_id.state != 'posted':
                self.expense_id = False
                return {'warning':
                    {
                        'title': _("Warning"),
                        'message': _('This expense sheet is invalid to reconcile')
                    }
                }
            self.amount = self.expense_id.total_amount
            self.is_expense = True
            self.name = _('Settlement from expense sheet: %(name)s', name=self.expense_id.name)
        else:
            self.amount = False
            self.is_expense = False
            self.name = False

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            if not self.name:
                self.name = self.product_id.display_name or ''
            account = self.product_id.product_tmpl_id._get_product_accounts()['expense']
            if account:
                self.expense_account_id = account
    def _set_is_reconcile(self):
        self.is_reconciled = True
        # Task 04: a reconciled line has completed its Finance Review cycle.
        self.filtered(lambda s: s.state != 'refused').write({'state': 'done'})


    def _settlement_line_with_bill(self, petty):
        petty_account_id = petty.petty_cash_journal_id.default_account_id
        petty_requester_id = petty.requester_partner_id

        for settlement in self:
            accounting_date = settlement.accounting_date or fields.Date.today()
            # Task 05: this entry only clears the vendor payable against the
            # petty cash account. Neither line is an expense line, so no
            # analytic distribution is applied here - the analytic already
            # sits on the expense lines of the vendor bill itself.

            payable_lines = settlement.bill_id.line_ids.filtered(
                lambda l: l.account_id.account_type == 'liability_payable'
                and not l.reconciled
                and not l.full_reconcile_id
                and l.amount_residual
            )
            if not payable_lines:
                raise ValidationError(_("There is no open payable line to reconcile for bill %s.") % settlement.bill_id.name)

            payable_line = payable_lines[0]
            payable_account = payable_line.account_id
            payable_partner = payable_line.partner_id or settlement.bill_id.partner_id
            residual_amount = sum(abs(line.amount_residual) for line in payable_lines.filtered(
                lambda l: l.account_id == payable_account and (l.partner_id or settlement.bill_id.partner_id) == payable_partner
            ))
            settlement_amount = settlement.amount
            if settlement_amount <= 0.0:
                raise ValidationError(_("Settlement amount must be greater than zero for bill %s.") % settlement.bill_id.name)
            if settlement_amount > residual_amount:
                raise ValidationError(_(
                    "Settlement amount %(amount)s is greater than the open amount %(residual)s for bill %(bill)s."
                ) % {
                    'amount': settlement_amount,
                    'residual': residual_amount,
                    'bill': settlement.bill_id.name,
                })

            expense_line = settlement.bill_id.invoice_line_ids.filtered(
                lambda l: l.account_id and l.account_id.account_type != 'liability_payable'
            )[:1]
            if not expense_line:
                expense_line = settlement.bill_id.line_ids.filtered(
                    lambda l: l.account_id and l.account_id.account_type != 'liability_payable' and l.debit
                )[:1]
            if expense_line and not settlement.expense_account_id:
                settlement.expense_account_id = expense_line.account_id

            adjustment_name = _('Settlements of (%(narration)s) from (%(document)s)',
                                narration=petty.narration, document=settlement.bill_id.name)
            move_id = self.env['account.move'].create({
                'narration': adjustment_name,
                'ref': _('Petty Cash [ %s ]', petty.name),
                'journal_id': petty.petty_cash_journal_id.id,
                'date': accounting_date,
                'move_type': 'entry',
                'line_ids': [
                    (0, 0, {
                        'name': adjustment_name,
                        'account_id': payable_account.id,
                        'debit': settlement_amount,
                        'credit': 0.0,
                        'partner_id': payable_partner.id,
                        'journal_id': petty.petty_cash_journal_id.id,
                        'date': accounting_date,
                        'analytic_distribution': False,
                    }),
                    (0, 0, {
                        'name': adjustment_name,
                        'account_id': petty_account_id.id,
                        'debit': 0.0,
                        'credit': settlement_amount,
                        'partner_id': petty_requester_id.id,
                        'journal_id': petty.petty_cash_journal_id.id,
                        'date': accounting_date,
                        'analytic_distribution': False,
                    })
                ]
            })
            move_id.action_post()
            petty._reconciliation(settlement.bill_id, move_id, payable_account)
            petty._reconciliation(petty._get_current_fund_move(), move_id, petty_account_id)
            settlement._set_is_reconcile()
        return True

    def _settlement_line_with_expense(self, petty):
        petty_account_id = petty.petty_cash_journal_id.default_account_id
        petty_suspense_account_id = petty.petty_cash_journal_id.default_account_id
        petty_requester_id = petty.requester_partner_id

        for settlement in self:
            accounting_date = settlement.accounting_date or fields.Date.today()
            # Task 05: these entries only move the payable / liquidity balance
            # around. No expense line is created here, so no analytic
            # distribution is applied - the analytic sits on the expense
            # document's own expense lines.

            expense_move_id = settlement.expense_id.account_move_id
            move_line = expense_move_id.line_ids.filtered(
                lambda l: l.account_id.account_type == 'liability_payable')
            payable_partner = move_line[0].mapped('partner_id')
            payable_account = move_line[0].mapped('account_id')
            payable_amount = sum(move_line.mapped('credit'))

            adjustment_suspense_name = _('Reconcile the expense sheet from ( %s )', petty.narration)

            line_ids_for_suspense = [
                (0, 0, {
                    'name': adjustment_suspense_name,
                    'account_id': payable_account.id,
                    'debit': payable_amount,
                    'credit': 0.0,
                    'partner_id': payable_partner.id,
                    'journal_id': petty.petty_cash_journal_id.id,
                    'date': accounting_date,
                    'analytic_distribution': False,
                }),
                (0, 0, {
                    'name': adjustment_suspense_name,
                    'account_id': petty_suspense_account_id.id,
                    'debit': 0.0,
                    'credit': settlement.amount,
                    'partner_id': petty_requester_id.id,
                    'journal_id': petty.petty_cash_journal_id.id,
                    'date': accounting_date,
                    'analytic_distribution': False,
                })
            ]
            move_suspense_id = self.env['account.move'].create({
                'narration': adjustment_suspense_name,
                'ref': _('Petty Cash [ %s ]', petty.name),
                'journal_id': petty.petty_cash_journal_id.id,
                'date': accounting_date,
                'move_type': 'entry',
                'line_ids': line_ids_for_suspense
            })
            if move_suspense_id:
                move_suspense_id.action_post()
                petty._reconciliation(expense_move_id, move_suspense_id, payable_account)

            adjustment_name = _('Settlements of (%(narration)s) from (%(document)s)',
                                narration=petty.narration, document=settlement.expense_id.name)

            line_ids_for_petty = [
                (0, 0, {
                    'name': adjustment_name,
                    'account_id': petty.petty_cash_journal_id.default_account_id.id,
                    'debit': settlement.amount,
                    'credit': 0.0,
                    'partner_id': petty_requester_id.id,
                    'journal_id': petty.petty_cash_journal_id.id,
                    'date': accounting_date,
                    'analytic_distribution': False,
                }),
                (0, 0, {
                    'name': adjustment_name,
                    'account_id': petty.petty_cash_journal_id.default_account_id.id,
                    'debit': 0.0,
                    'credit': settlement.amount,
                    'partner_id': petty_requester_id.id,
                    'journal_id': petty.petty_cash_journal_id.id,
                    'date': accounting_date,
                    'analytic_distribution': False,
                })
            ]
            move_petty_id = self.env['account.move'].create({
                'narration': adjustment_name,
                'ref': _('Settlements of %s', petty.name),
                'journal_id': petty.petty_cash_journal_id.id,
                'date': accounting_date,
                'move_type': 'entry',
                'line_ids': line_ids_for_petty
            })
            if move_petty_id:
                move_petty_id.action_post()
                petty._reconciliation(petty._get_current_fund_move(), move_petty_id, petty_account_id)
                settlement._set_is_reconcile()
        return True

    def _settlement_line_without_bill(self, petty):
        petty_account_id = petty.petty_cash_journal_id.default_account_id
        petty_requester_id = petty.requester_partner_id
        petty_journal = petty.petty_cash_journal_id
        company = petty.company_id or self.env.company
        currency = company.currency_id
        line_ids = []
        adjustment_name = _('Settlements of ( %s )', petty.narration)

        accounting_date = self[0].accounting_date or fields.Date.today() if self else fields.Date.today()

        total_credit = 0.0  # <-- الجديد

        for settlement in self:
            analytic_dist = settlement.sudo().analytic_distribution or False

            tax_result = settlement._compute_tax_result(petty)

            expense_amount = tax_result['total_excluded']
            missing_tax_accounts = [
                tax_vals['name']
                for tax_vals in tax_result['taxes']
                if not currency.is_zero(tax_vals['amount'])
                and not tax_vals['account_id']
            ]
            if missing_tax_accounts:
                raise UserError(_(
                    'Configure an account on the invoice tax repartition lines '
                    'for: %(taxes)s.',
                    taxes=', '.join(missing_tax_accounts),
                ))

            line_ids.append((0, 0, {
                'name': settlement.name,
                'account_id': settlement.expense_account_id.id,
                'debit': expense_amount,
                'credit': 0.0,
                'partner_id': petty_requester_id.id,
                'journal_id': petty_journal.id,
                'date': accounting_date,
                'analytic_distribution': analytic_dist,
                'tax_ids': [(6, 0, settlement.tax_ids.ids)],
            }))

            total_credit += tax_result['total_included']

        line_ids.append((0, 0, {
            'name': adjustment_name,
            'account_id': petty_account_id.id,
            'debit': 0.0,
            'credit': total_credit,
            'partner_id': petty_requester_id.id,
            'journal_id': petty_journal.id,
            'date': accounting_date,
            'analytic_distribution': False,
        }))

        move_id = self.env['account.move'].with_company(company).with_context(
            allowed_company_ids=[company.id],
            force_price_include=False,
        ).create({
            'narration': _('Settlements of ( %s )', petty.narration),
            'ref': _('Settlements of %s', petty.name),
            'journal_id': petty_journal.id,
            'date': accounting_date,
            'move_type': 'entry',
            'company_id': company.id,
            'line_ids': line_ids
        })

        if move_id:
            move_id.action_post()
            petty._reconciliation(
                petty._get_current_fund_move(),
                move_id,
                petty_account_id
            )
            self._set_is_reconcile()

        return True
    def _compute_tax_result(self, petty):

        self.ensure_one()
        if not self.tax_ids or not self.amount:
            return {
                'total_excluded': self.amount,
                'total_included': self.amount,
                'taxes': [],
                'base_tags': [],
            }

        company = petty.company_id or self.env.company
        currency = company.currency_id
        partner = petty.requester_partner_id
        invalid_taxes = self.tax_ids.filtered(
            lambda tax: tax.company_id and tax.company_id != company
        )
        if invalid_taxes:
            raise ValidationError(_(
                'The following taxes do not belong to company %(company)s: '
                '%(taxes)s.',
                company=company.display_name,
                taxes=', '.join(invalid_taxes.mapped('display_name')),
            ))

        taxes = self.tax_ids.with_context(
            force_price_include=self.is_amount_includes_tax,
        )
        return taxes.compute_all(
            price_unit=self.amount,
            currency=currency,
            quantity=1.0,
            product=self.product_id,
            partner=partner,
            is_refund=False,
            handle_price_include=True,
        )
