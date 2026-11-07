
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from odoo.exceptions import AccessError
from odoo.addons.web.controllers.utils import clean_action

import ast
import base64
import logging
from io import BytesIO

_logger = logging.getLogger(__name__)
try:
    import pandas as pd
except ImportError:
    pd = None
    _logger.warning("pandas library is not installed. Excel import feature will not work.")



try:
    from num2words import num2words
except ImportError:
    _logger.warning("The num2words python library is not installed, amount-to-text features won't be fully available.")
    num2words = None


class pettyCashRequest(models.Model):
    _name = "petty.cash.request"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = "id desc"
    _description = "Petty Cash Request"
    name = fields.Char('Name', default=lambda self: _('New'))
    narration = fields.Char('Narration', required=True)
    order_date = fields.Date('Order Date', default=lambda self: fields.Date.today())
    approve_date = fields.Date('Approve Date')

    by_employee = fields.Boolean('By Employee', default=True)
    use_analytic = fields.Boolean('Use Analytic', default=False)

    requester_emp_id = fields.Many2one(
        'hr.employee',
        'Employee Requester',
        default=lambda self: self.env.user.employee_id.id,
        tracking=True
    )
    requester_partner_id = fields.Many2one('res.partner', 'Partner Requester')
    move_id = fields.Many2one('account.move', 'Journal Entry')
    operation_account_id = fields.Many2one(
        'account.account',
        'Operation Account',
        default=lambda self: self.env.company.account_journal_suspense_account_id
    )
    operation_journal_id = fields.Many2one(
        'account.journal',
        'Operation Journal',
        domain=[('type', 'in', ('bank', 'cash'))]
    )
    petty_cash_journal_id = fields.Many2one(
        'account.journal',
        'Petty Cash Journal',
        compute="_compute_petty_cash_journal",
        store=True,
        readonly=False,
    )
    user_id = fields.Many2one(
        'res.users',
        string='User',
        required=False,
        default=lambda self: self.env.user
    )

    settlement_ids = fields.One2many('petty.cash.settlement', 'petty_request_id', string='Settlements')

    # Task 05: the request no longer carries its own analytic distribution.
    # Analytic distribution is entered per settlement line and is applied only
    # to that line's expense journal item - never to liquidity / petty cash /
    # custody / counterpart lines.

    amount = fields.Float('Petty Cash Amount', required=True)
    settlement_amount = fields.Float('Settlement Amount', compute='_compute_settlement_amount', store=True, compute_sudo=False)
    different_amount = fields.Float('Different Amount', compute='_compute_different_amount', store=True)
    replenishment_total_amount = fields.Float(
        string='Total replenishment',
        compute='_compute_replenishment_total_amount',
        store=False,
        compute_sudo=False,
        help='Sum of settlement lines already covered by a replenishment (substitution) entry.',
    )
    in_settlement = fields.Boolean('In Settlement', default=False, compute='_compute_in_settlement', store=False, compute_sudo=False)
    has_request_not_reconciled = fields.Boolean(
        'Has Request Not Reconciled',
        default=False,
        compute='_compute_has_request_not_reconciled'
    )
    settlement_state = fields.Selection([
        ('no', 'Not Yet'),
        ('partial', 'Partial Settlement'),
        ('full', 'Full Settlement')
    ], string='Settlement Status', default='no', compute='_set_settlement_status')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('request', 'Requested'),
        ('approval', 'Approval'),
        ('complete', 'Complete'),
        ('reject', 'Rejected')
    ], string='Status', default='draft', tracking=True)

    replenishment_requested = fields.Boolean(
        'Replenishment Requested', default=False, tracking=True,
    )
    replenishment_approved = fields.Boolean(
        'Replenishment Approved', default=False, tracking=True,
    )

    note = fields.Text(string='Notes')
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'petty_cash_request_attachment_rel',
        'request_id',
        'attachment_id',
        string='Attachments'
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        required=True,
        index=True
    )
    settlement_date_from = fields.Date(
        string='Settlement Date From',
        help='Filter settlements from this date'
    )
    settlement_date_to = fields.Date(
        string='Settlement Date To',
        help='Filter settlements to this date'
    )
    excel_import_file = fields.Binary('Excel File', attachment=True)
    excel_import_filename = fields.Char('Filename')
    accounting_date = fields.Date(
        string='Accounting Date',
        help='Date to be used for accounting entries instead of current date',
        default=fields.Date.today
    )
    settlement_amount_reconcile = fields.Float(
        'Settlement Amount (Reconciled)',
        compute='_compute_settlement_amount_reconcile',
        store=True,
        compute_sudo=False,
        help='Sum of settlement lines that have been reconciled only'
    )
    refuse = fields.Boolean(
        string='Refuse Settlement Line',
        default=False,
        tracking=True,
    )
    refuse_reason = fields.Text(
        string='Refusal Reason',
        tracking=True,
    )
    petty_cash_type = fields.Selection([
        ('permanent', 'Permanent Petty Cash'),
        ('temporary', 'Temporary Petty Cash')
    ], string='Petty Cash Type', required=True, tracking=True)
    petty_cash_checkbox = fields.Boolean(
        string="Permanent Petty Cash"
    )
    temporary_petty_cash = fields.Boolean(
        string="Temporary Petty Cash"
    )
    def action_filter_settlements_by_date(self):
        """Filter settlements by date range"""
        self.ensure_one()

        domain = [('petty_request_id', '=', self.id)]

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
            'context': {
                'default_petty_request_id': self.id,
                'group_by': ['accounting_date'],
            }
        }

    # ========================================================================
    # ACCESS CONTROL METHODS
    # ========================================================================
    @api.depends('company_id', 'petty_cash_type')
    def _compute_petty_cash_journal(self):
        for rec in self:
            journal = False

            if rec.petty_cash_type == 'permanent':
                journal = self.env['account.journal'].search([
                    ('petty_cash_checkbox', '=', True),
                    ('company_id', '=', rec.company_id.id)
                ], limit=1)
                if not journal:
                    rec.petty_cash_type = False

            elif rec.petty_cash_type == 'temporary':
                journal = self.env['account.journal'].search([
                    ('temporary_petty_cash', '=', True),
                    ('company_id', '=', rec.company_id.id)
                ], limit=1)
                if not journal:
                    rec.petty_cash_type = False

            rec.petty_cash_journal_id = journal
    @api.onchange('petty_cash_type', 'company_id')
    def _onchange_petty_cash_type(self):
        self._compute_petty_cash_journal()
        if not self.petty_cash_type:
            self.petty_cash_journal_id = False
    @api.constrains('petty_cash_checkbox', 'temporary_petty_cash')
    def _check_petty_cash_type_unique(self):
        for rec in self:
            if rec.petty_cash_checkbox and rec.temporary_petty_cash:
                raise ValidationError(_(
                    "A petty cash request cannot be both Permanent and Temporary. "
                    "Please select only one type."
                ))

    def _get_petty_cash_role(self):
        '''Get current user petty cash role'''
        user = self.env.user

        # Check if user is administrator
        if user.has_group('petty_cash.group_petty_cash_administrator'):
            return 'administrator'

        # Check if user is user
        if user.has_group('petty_cash.group_petty_cash_user'):
            return 'user'

        return None

    @api.model
    def _get_allowed_employee_ids(self):
        '''Get list of employee IDs that current user can create requests for'''
        role = self._get_petty_cash_role()

        if not role:
            return []

        # Administrator can access all employees
        if role == 'administrator':
            return self.env['hr.employee'].search([]).ids

        # User role
        user = self.env.user
        employee = self.env['hr.employee'].search([('user_id', '=', user.id)], limit=1)

        if not employee:
            return []

        allowed_employees = [employee.id]

        # Get subordinates (employees where current employee is their manager)
        subordinates = self.env['hr.employee'].search([
            ('parent_id', '=', employee.id)
        ])
        allowed_employees.extend(subordinates.ids)

        return allowed_employees

    @api.model_create_multi
    def create(self, vals_list):
        '''Override create to check access rights on requester_emp_id'''
        allowed_ids = self._get_allowed_employee_ids()
        for vals in vals_list:
            requester_emp_id = vals.get('requester_emp_id')
            if requester_emp_id and allowed_ids and requester_emp_id not in allowed_ids:
                raise AccessError(_(
                    'You are not allowed to create requests for this employee. '
                    'You can only create requests for yourself and your subordinates.'
                ))
            if not vals.get('name', False) or vals['name'] == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('petty.cash.request') or _('New')

        return super().create(vals_list)

    def write(self, vals):
        '''Override write to check access rights on requester_emp_id'''
        # Check requester_emp_id access
        if 'requester_emp_id' in vals:
            allowed_ids = self._get_allowed_employee_ids()
            for record in self:
                if allowed_ids and vals['requester_emp_id'] not in allowed_ids:
                    raise AccessError(_(
                        'You are not allowed to change requester to this employee. '
                        'You can only select yourself or your subordinates.'
                    ))

        return super().write(vals)

    @api.onchange('requester_emp_id')
    def _onchange_requester_emp_id(self):
        result = {}

        allowed_ids = self._get_allowed_employee_ids()

        if allowed_ids:
            result['domain'] = {
                'requester_emp_id': [('id', 'in', allowed_ids)]
            }

        if self.requester_emp_id:

            # Set partner from employee
            if hasattr(self.requester_emp_id, 'address_home_id') and self.requester_emp_id.address_home_id:
                self.requester_partner_id = self.requester_emp_id.address_home_id
            elif hasattr(self.requester_emp_id, 'work_contact_id') and self.requester_emp_id.work_contact_id:
                self.requester_partner_id = self.requester_emp_id.work_contact_id
            elif self.requester_emp_id.user_id and self.requester_emp_id.user_id.partner_id:
                self.requester_partner_id = self.requester_emp_id.user_id.partner_id
            else:
                self.requester_partner_id = False
        else:
            self.requester_partner_id = False

        return result if result else {}
    # ========================================================================
    # ORIGINAL METHODS (unchanged)
    # ========================================================================

    def action_request(self):
        if self.amount <= 0:
            raise UserError(_('The petty cash amount must be greater than "0"'))
        self.state = 'request'

    @api.constrains('requester_emp_id')
    def _check_unique_requester_employee(self):
        for rec in self:
            if not rec.requester_emp_id:
                continue
            duplicate = self.search(
                [
                    ('requester_emp_id', '=', rec.requester_emp_id.id),
                    ('id', '!=', rec.id),
                ],
                limit=1,
            )
            if duplicate:
                raise ValidationError(
                    _('Only one petty cash request is allowed per requesting employee.')
                )

    @api.constrains('settlement_ids')
    def _check_settlements_amount(self):
        """Only unreplenished lines count against the imprest ceiling."""
        for rec in self:
            # Exclude lines that are replenished or refused
            open_amount = sum(
                rec.settlement_ids.filtered(lambda s: not s.replenished and not s.refuse).mapped('total_with_tax')
            )
            if open_amount > rec.amount:
                raise ValidationError(_(
                    "You cannot settle a balance higher than the petty cash in the "
                    "current cycle."
                ))
    @api.depends('settlement_ids.amount', 'settlement_ids.replenished', 'settlement_ids.refuse')
    def _compute_settlement_amount(self):
        """Current imprest cycle: sum only settlements not yet replenished and not refused."""
        for rec in self:
            rec.settlement_amount = sum(
                rec.settlement_ids.filtered(lambda s: not s.replenished and not s.refuse).mapped('total_with_tax')
            )
    @api.depends(
        'settlement_ids.amount',
        'settlement_ids.is_reconciled',
        'settlement_ids.replenished',
        'settlement_ids.refuse',
    )
    def _compute_settlement_amount_reconcile(self):
        """Current cycle reconciled amount, excluding replenished/refused lines."""
        for rec in self:
            rec.settlement_amount_reconcile = sum(
                rec.settlement_ids.filtered(
                    lambda s: s.is_reconciled and not s.replenished and not s.refuse
                ).mapped('total_with_tax')
            )

    @api.depends('settlement_ids.is_reconciled', 'settlement_ids.refuse')
    def _compute_in_settlement(self):
        for rec in self:
            # Any settlement line that is not yet reconciled and not rejected
            rec.in_settlement = any(
                not settlement.is_reconciled and not settlement.refuse
                for settlement in rec.settlement_ids
            )

    @api.depends('amount', 'settlement_ids.total_with_tax', 'settlement_ids.is_reconciled', 'settlement_ids.refuse')
    def _compute_different_amount(self):
        for rec in self:
            reconciled_amount = sum(
                rec.settlement_ids.filtered(
                    lambda s: s.is_reconciled and not s.refuse
                ).mapped('total_with_tax')
            )
            rec.different_amount = rec.amount - reconciled_amount

    @api.depends('settlement_ids.total_with_tax', 'settlement_ids.replenished', 'settlement_ids.refuse')
    def _compute_replenishment_total_amount(self):
        for rec in self:
            rec.replenishment_total_amount = sum(
                s.total_with_tax for s in rec.settlement_ids if s.replenished and not s.refuse
            )

    def _set_settlement_status(self):
        for rec in self:
            if 0 < rec.settlement_amount < rec.amount:
                rec.settlement_state = 'partial'
            elif rec.settlement_amount >= rec.amount:
                rec.settlement_state = 'full'
            else:
                rec.settlement_state = 'no'
    def _compute_has_request_not_reconciled(self):
        for rec in self:
            rec.has_request_not_reconciled = False
            all_partner_request = self.search([
                ('id', '!=', rec.id),
                ('requester_partner_id', '=', rec.requester_partner_id.id)
            ])
            if (all_partner_request and
                    all_partner_request.filtered(lambda r: r.settlement_state != 'full') and
                    rec.state == 'draft'):
                rec.has_request_not_reconciled = True

    @api.onchange('operation_journal_id')
    def _onchange_operation_journal_id(self):
        if self.operation_journal_id:
            self.operation_account_id = self.operation_journal_id.default_account_id
        else:
            self.operation_account_id = self.env.company.account_journal_suspense_account_id

    def _prepare_debit_data(self, name, debit_account, debit_amount, partner):
        # Task 05: liquidity / petty cash / custody / counterpart lines never
        # carry an analytic distribution.
        petty_journal = self.petty_cash_journal_id
        return {
            'name': name,
            'partner_id': partner.id,
            'account_id': debit_account.id,
            'journal_id': petty_journal.id,
            'date': fields.Date.today(),
            'analytic_distribution': False,
            'debit': debit_amount,
            'credit': 0.0,
        }

    def _prepare_credit_data(self, name, credit_account, credit_amount, partner):
        # Task 05: liquidity / petty cash / custody / counterpart lines never
        # carry an analytic distribution.
        petty_journal = self.petty_cash_journal_id
        return {
            'name': name,
            'partner_id': partner.id,
            'account_id': credit_account.id,
            'journal_id': petty_journal.id,
            'date': fields.Date.today(),
            'analytic_distribution': False,
            'debit': 0.0,
            'credit': credit_amount,
        }

    def _prepare_move_lines_data(self, operation_account_id, amount, line_label=None):
        petty_journal = self.petty_cash_journal_id
        label = line_label or self.narration
        return [
            (0, 0, self._prepare_debit_data(
                label,
                petty_journal.default_account_id,
                amount,
                self.requester_partner_id
            )),
            (0, 0, self._prepare_credit_data(
                label,
                operation_account_id,
                amount,
                self.requester_partner_id
            ))
        ]

    def _get_replenishable_settlements(self):
        """Settlement lines eligible for the next replenishment cycle."""
        self.ensure_one()
        return self.settlement_ids.filtered(
            lambda s: s.is_reconciled and not s.replenished and not s.refuse and (
                s.expense_account_id or s.expense_id or s.bill_id
            )
        )

    def _get_current_fund_move(self):
        self.ensure_one()

        funding_moves = self.move_id | self.settlement_ids.mapped(
            'replenishment_move_id'
        ).filtered(lambda m: m and m.state == 'posted')

        return funding_moves
    def _get_replenishment_spent_amount(self):
        """Sum of spent funds in the current cycle (excluding return/intemperance lines)."""
        self.ensure_one()
        return sum(self._get_replenishable_settlements().mapped('total_with_tax'))

    def _create_account_move(self):
        self.ensure_one()

        if self.move_id:
            raise UserError(_("You have already created a journal entry."))

        # Verify company matching between the request and the journal
        if self.petty_cash_journal_id.company_id and self.petty_cash_journal_id.company_id != self.company_id:
            raise UserError(_(
                "The Petty Cash Journal '%s' belongs to company '%s' but the request belongs to company '%s'. "
                "Please select a journal that belongs to the same company as the request.",
                self.petty_cash_journal_id.name,
                self.petty_cash_journal_id.company_id.name,
                self.company_id.name
            ))

        move_vals = {
            'narration': self.narration,
            'ref': self.name,
            'journal_id': self.petty_cash_journal_id.id,
            'date': fields.Date.today(),
            'move_type': 'entry',
            'company_id': self.company_id.id,
        }


        move = self.env['account.move'].with_company(self.company_id).with_context(
            allowed_company_ids=[self.company_id.id]
        ).sudo().create(move_vals)

        return move
    def action_approve(self):
        for rec in self:
            if rec.has_request_not_reconciled:
                raise ValidationError(_(
                    "%(partner)s has already petty cash isn't full reconciled",
                    partner=rec.requester_partner_id.name,
                ))
            if not rec.petty_cash_journal_id:
                raise UserError(_("Unknown Petty Cash Journal."))


            if rec.petty_cash_journal_id.company_id and rec.petty_cash_journal_id.company_id != rec.company_id:
                raise UserError(_(
                    "The Petty Cash Journal '%s' belongs to company '%s' but the request belongs to company '%s'. "
                    "Please select a journal that belongs to the same company.",
                    rec.petty_cash_journal_id.name,
                    rec.petty_cash_journal_id.company_id.name,
                    rec.company_id.name
                ))

            if not rec.narration:
                raise UserError(_("Add the narration of petty cash."))
            if not rec.amount:
                raise UserError(_("Amount of petty cash must be added."))

            move_id = rec._create_account_move()
            if move_id:
                rec.sudo().write({
                    'move_id': move_id.id,
                    'state': 'approval',
                    'approve_date': fields.Date.today()
                })
        return True
    def action_reject(self):
        for rec in self:
            if rec.move_id:
                # NOTE (upgrade): the original code interpolated the *recordset*
                # into the SQL string ("... id=account.move(5,)"), which raises a
                # SyntaxError in PostgreSQL. Same intent, correct parameter.
                move_id = rec.move_id.id
                rec.move_id = False
                self.env.cr.execute("DELETE FROM account_move WHERE id=%s", (move_id,))
                self.env['account.move'].invalidate_model()
            rec.state = 'reject'
            rec.replenishment_requested = False
            rec.replenishment_approved = False
        return True

    def action_draft(self):
        for rec in self:
            rec.state = 'draft'
            rec.replenishment_requested = False
            rec.replenishment_approved = False
        return True

    def action_paid(self):
        for rec in self:
            if not rec.move_id:
                raise UserError(_("You should create an journal entry first."))
            if not rec.operation_journal_id:
                raise UserError(_("You should add operation journal."))
            if not rec.operation_journal_id.default_account_id:
                raise UserError(_("The operation journal must have a default account."))
            rec.move_id.write({
                'line_ids': rec._prepare_move_lines_data(
                    rec.operation_journal_id.default_account_id,
                    rec.amount,
                )
            })
            rec.move_id.action_post()
            rec.state = 'complete'
        return True

    def action_request_replenishment(self):
        """Employee requests petty-cash replenishment (استعاضة)."""
        self.ensure_one()
        if self.replenishment_requested and not self.replenishment_approved:
            raise UserError(_("A replenishment request has already been submitted and is awaiting approval."))
        self.write({
            'replenishment_requested': True,
            'replenishment_approved': False,
        })
        self.message_post(body=_("Petty cash replenishment requested."))

    def action_approve_substitution(self):
        """Accountant approves the replenishment (Substitution) and opens the operations wizard."""
        self.ensure_one()
        if not self.replenishment_requested:
            raise UserError(_("No replenishment has been requested for this record."))
        if self.replenishment_approved:
            raise UserError(_("This replenishment has already been approved."))
        spent = self._get_replenishment_spent_amount()
        if spent <= 0:
            raise UserError(_(
                "Cannot approve replenishment: there is no settled expense amount. "
                "Add settlement lines with an expense account (or link a bill / expense sheet) and confirm settlement first."
            ))
        # self.write({'replenishment_approved': True})
        self.message_post(body=_("Petty cash replenishment approved (Substitution)."))
        return self.with_context(petty_operation='replenishment').open_wizard_operation()

    def _reconciliation(self, petty_move_id, reconcile_move_id, petty_account_id):
        if not petty_move_id or not reconcile_move_id:
            return True

        if reconcile_move_id.state != 'posted':
            reconcile_move_id.action_post()

        lines = (
                petty_move_id.line_ids.filtered(
                    lambda l: l.move_id.state == 'posted'
                              and l.account_id == petty_account_id
                              and not l.reconciled
                              and not l.full_reconcile_id
                              and l.amount_residual
                ) +
                reconcile_move_id.line_ids.filtered(
                    lambda l: l.move_id.state == 'posted'
                              and l.account_id == petty_account_id
                              and not l.reconciled
                              and not l.full_reconcile_id
                              and l.amount_residual
                )
        )

        if len(lines) >= 2:
            lines.reconcile()

        return True
    def _confirm_settlement_lines(self, settlements):
        """Post only the supplied settlement lines for this petty cash request."""
        self.ensure_one()
        foreign_settlements = settlements.filtered(
            lambda settlement: settlement.petty_request_id != self
        )
        if foreign_settlements:
            raise ValidationError(_(
                'All settlement lines must belong to the same petty cash request.'
            ))
        if not self._get_current_fund_move():
            raise UserError(_("You should create a journal entry first."))

        pending_settlements = settlements.filtered(
            lambda settlement: not settlement.is_reconciled and not settlement.refuse
        )
        # Task 04: nothing is posted before the Finance Review has been passed.
        pending_settlements._ensure_finance_reviewed()
        settlements_with_expenses = pending_settlements.filtered(
            lambda settlement: settlement.expense_id
            and settlement.expense_id.state == 'posted'
        )
        settlements_with_bills = pending_settlements.filtered(
            lambda settlement: settlement.bill_id
            and settlement.bill_id.payment_state in ('not_paid', 'partial')
        )
        settlements_without_documents = pending_settlements.filtered(
            lambda settlement: not settlement.bill_id and not settlement.expense_id
        )

        if settlements_with_expenses:
            settlements_with_expenses._settlement_line_with_expense(self)

        if settlements_with_bills:
            settlements_with_bills._settlement_line_with_bill(self)

        if settlements_without_documents:
            if any(
                not line.expense_account_id
                for line in settlements_without_documents
            ):
                raise ValidationError(_(
                    "You should add expense account for any settlement line "
                    "that has no bill or expense."
                ))
            settlements_without_documents._settlement_line_without_bill(self)

        self._compute_settlement_amount_reconcile()
        return True

    def confirm_settlement(self):
        for petty in self:
            if not petty._get_current_fund_move():
                raise UserError(_("You should create a journal entry first."))
            # استبعد الـ lines المرفوضة من كل المجموعات
            # Task 04: nothing is posted before the Finance Review has been passed.
            petty.settlement_ids.filtered(
                lambda s: not s.is_reconciled and not s.refuse
            )._ensure_finance_reviewed()
            settlements_with_expenses = petty.settlement_ids.filtered(
                lambda s: not s.is_reconciled and not s.refuse and s.expense_id and s.expense_id.state == 'posted'
            )
            settlements_with_bills = petty.settlement_ids.filtered(
                lambda s: not s.is_reconciled and not s.refuse and s.bill_id and s.bill_id.payment_state in ('not_paid', 'partial')
            )
            settlements_without_bills = petty.settlement_ids.filtered(
                lambda s: not s.bill_id and not s.expense_id and not s.is_reconciled and not s.refuse
            )

            if settlements_with_expenses:
                settlements_with_expenses._settlement_line_with_expense(petty)

            if settlements_with_bills:
                settlements_with_bills._settlement_line_with_bill(petty)

            if settlements_without_bills:
                if any(not line.expense_account_id for line in settlements_without_bills):
                    raise ValidationError(_(
                        "You should add expense account for any settlement line has not bill or expense"
                    ))
                settlements_without_bills._settlement_line_without_bill(petty)

            petty._compute_settlement_amount_reconcile()

        return True
    # ========================================================================
    # Task 04 - Finance Review on the settlement cycle
    # ========================================================================
    settlement_review_pending = fields.Boolean(
        string='Settlements Awaiting Finance Review',
        compute='_compute_settlement_review_pending',
    )

    @api.depends('settlement_ids.state', 'settlement_ids.refuse')
    def _compute_settlement_review_pending(self):
        for rec in self:
            rec.settlement_review_pending = any(
                line.state in ('draft', 'finance_review') and not line.refuse
                for line in rec.settlement_ids
            )

    def _settlements_awaiting_review(self):
        self.ensure_one()
        return self.settlement_ids.filtered(
            lambda s: s.state in ('draft', 'finance_review') and not s.refuse
        )

    def action_settlements_submit_finance_review(self):
        """Send the pending settlement lines of this request to Finance Review."""
        self.ensure_one()
        lines = self.settlement_ids.filtered(
            lambda s: s.state == 'draft' and not s.refuse
        )
        if not lines:
            raise UserError(_("There is no draft settlement line to submit."))
        lines.action_submit_finance_review()
        self.message_post(body=_("%s settlement line(s) sent to Finance Review.", len(lines)))
        return True

    def action_settlements_finance_approve(self):
        """Finance Review approval for the settlement lines of this request.

        Restricted to the standard accounting group allowed to create journal
        entries (account.group_account_user). No project / department /
        purchase manager approval is involved.
        """
        self.ensure_one()
        lines = self._settlements_awaiting_review()
        if not lines:
            raise UserError(_("There is no settlement line waiting for Finance Review."))
        lines.action_finance_approve()
        self.message_post(body=_("Finance Review approved for %s settlement line(s).", len(lines)))
        return True

    def _get_context(self):
        """Build the wizard context based on the current workflow intent.

        An explicit intent may be supplied via ``context['petty_operation']``:
        ``'replenishment'``, ``'return'``, ``'intemperance'`` or ``'initial'``.
        Otherwise it is inferred for backwards compatibility.
        """
        self.ensure_one()
        context = {'default_petty_request_id': self.id}
        intent = self.env.context.get('petty_operation')

        if intent == 'replenishment' or (intent is None and self.replenishment_approved):
            context.update({
                'default_amount': self._get_replenishment_spent_amount(),
                'default_is_replenishment': True,
                'default_is_return': False,
                'default_is_intemperance': False,
            })
            return context

        if intent == 'intemperance' or (intent is None and self.settlement_amount > self.amount):
            context.update({
                'default_amount': abs(self.different_amount),
                'default_is_intemperance': True,
            })
            return context

        if intent == 'return' or (intent is None and 0 < self.settlement_amount < self.amount):
            context.update({
                'default_amount': self.different_amount,
                'default_is_return': True,
            })
            return context

        context.update({
            'default_amount': self.amount,
        })
        return context

    def _petty_cash_companies_context(self):
        """Put the request company first so accounting records (accounts, journals) resolve under the right company."""
        self.ensure_one()
        if not self.company_id:
            return {}
        allowed = self.env.user.company_ids.ids
        if self.company_id.id not in allowed:
            raise UserError(_(
                "Your user is not allowed to use company %(co)s. Add it on the user's "
                "Allowed Companies, or switch to a user that has access to this company.",
                co=self.company_id.display_name,
            ))
        others = [cid for cid in allowed if cid != self.company_id.id]
        return {'allowed_company_ids': [self.company_id.id] + others}

    def open_wizard_operation(self):
        if not self.move_id:
            raise UserError(_("You should create an journal entry first."))
        self.ensure_one()
        ctx = dict(self.env.context)
        ctx.update(self._get_context())
        ctx.update(self._petty_cash_companies_context())
        return {
            'name': _('Operations'),
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'petty.cash.operations',
            'view_id': self.env.ref("petty_cash.view_petty_cash_operation_form", False).id,
            'target': 'new',
            'context': ctx,
        }

    def petty_cash_settlement(self):
        self.ensure_one()
        ctx = dict(self.env.context)
        ctx.update(self._petty_cash_companies_context())
        return {
            'name': _('Confirm'),
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'petty.cash.request',
            'view_id': self.env.ref("petty_cash.view_petty_confirm_settlement_form", False).id,
            'target': 'new',
            'res_id': self.id,
            'context': ctx,
        }

    def open_reconcile_view(self):
        """Journal items for initial funding + replenishment moves, plus reconciliation graph.

        Core ``account.move.line.open_reconcile_view`` only keeps lines with partial
        matches; replenishment liquidity lines are often still open, so they never
        appeared. We union the standard matched graph with any unmatched lines that
        belong to this request's funding or replenishment moves.
        """
        self.ensure_one()
        if not self.move_id:
            raise UserError(_("You should create a journal entry first."))
        petty_account = self.petty_cash_journal_id.default_account_id
        if not petty_account:
            raise UserError(_("The petty cash journal must have a default liquidity account."))
        repl_moves = self.settlement_ids.mapped('replenishment_move_id').filtered(lambda m: m)
        seed = self.move_id.line_ids | repl_moves.line_ids
        seed = seed.filtered(lambda l: l.display_type not in ('line_section', 'line_note'))
        matched = seed._all_reconciled_lines().filtered(
            lambda l: l.matched_debit_ids or l.matched_credit_ids
        )
        unmatched_seed = seed.filtered(
            lambda l: not l.matched_debit_ids and not l.matched_credit_ids
        )
        line_ids = matched | unmatched_seed
        action = self.env['ir.actions.act_window']._for_xml_id(
            'account.action_account_moves_all_grouped_matching'
        )
        action = dict(action)
        action['domain'] = [
            ('id', 'in', line_ids.ids),
            ('account_id', '=', petty_account.id),
        ]
        action = clean_action(action, self.env)
        raw_ctx = action.get('context') or {}
        if isinstance(raw_ctx, str):
            raw_ctx = ast.literal_eval(raw_ctx.strip()) if raw_ctx.strip() else {}
        elif not isinstance(raw_ctx, dict):
            raw_ctx = {}
        ctx = dict(raw_ctx)
        ctx.update(self._petty_cash_companies_context())
        action['context'] = ctx
        return action

    def action_import_excel_settlements(self):
        """Open wizard to upload Excel file for importing settlements"""
        return {
            'name': _('Import Settlements from Excel'),
            'type': 'ir.actions.act_window',
            'res_model': 'petty.cash.import.wizard',
            'view_mode': 'form',
            'domain': [],
            'target': 'new',
            'context': {
                'default_petty_request_id': self.id,
            }
        }

    def import_settlements_from_excel(self, file_content, filename):
        """Import settlement lines from Excel file"""
        if not pd:
            raise UserError(_('pandas library is not installed. Please contact your administrator.'))

        try:
            # Read Excel file
            excel_data = BytesIO(base64.b64decode(file_content))

            # Attempt to read the file using different formats
            if filename.endswith('.xlsx'):
                df = pd.read_excel(excel_data, engine='openpyxl')
            elif filename.endswith('.xls'):
                df = pd.read_excel(excel_data, engine='xlrd')
            elif filename.endswith('.csv'):
                df = pd.read_csv(excel_data)
            else:
                raise UserError(_('Unsupported file format. Please use .xlsx, .xls, or .csv'))

            # Verify that the required columns exist
            required_columns = ['name', 'amount']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                raise UserError(_('Missing required columns: %s') % ', '.join(missing_columns))

            # Process each row and create a settlement line
            for index, row in df.iterrows():
                settlement_vals = {
                    'name': str(row.get('name', '')),
                    'amount': float(row.get('amount', 0)),
                    'petty_request_id': self.id,
                }

                # Add the expense account if it exists
                if 'expense_account_id' in df.columns and pd.notna(row.get('expense_account_id')):
                    # Search for the expense account by code or name
                    account_code = str(row.get('expense_account_id'))
                    account = self.env['account.account'].search([
                        '|',
                        ('code', '=', account_code),
                        ('name', 'ilike', account_code),
                        ('account_type', '=', 'expense')
                    ], limit=1)
                    if account:
                        settlement_vals['expense_account_id'] = account.id

                # Add the product if it exists
                if 'product_id' in df.columns and pd.notna(row.get('product_id')):
                    product_name = str(row.get('product_id'))
                    product = self.env['product.product'].search([
                        '|',
                        ('default_code', '=', product_name),
                        ('name', 'ilike', product_name)
                    ], limit=1)
                    if product:
                        settlement_vals['product_id'] = product.id

                # Add the accounting date if it exists
                if 'accounting_date' in df.columns and pd.notna(row.get('accounting_date')):
                    settlement_vals['accounting_date'] = row.get('accounting_date')

                # Add bill_id if present
                if 'bill_id' in df.columns and pd.notna(row.get('bill_id')):
                    bill_name = str(row.get('bill_id'))
                    bill = self.env['account.move'].search([
                        ('name', '=', bill_name),
                        ('move_type', '=', 'in_invoice')
                    ], limit=1)
                    if bill:
                        settlement_vals['bill_id'] = bill.id
                        settlement_vals['is_bill'] = True

                # Add expense_id if present
                if 'expense_id' in df.columns and pd.notna(row.get('expense_id')):
                    expense_name = str(row.get('expense_id'))
                    expense = self.env['hr.expense'].search([
                        ('name', '=', expense_name)
                    ], limit=1)
                    if expense:
                        settlement_vals['expense_id'] = expense.id
                        settlement_vals['is_expense'] = True

                # Validate data
                if settlement_vals['amount'] <= 0:
                    continue

                if not settlement_vals['name']:
                    settlement_vals['name'] = _('Imported settlement line %s') % (index + 1)

                # Create settlement line
                self.env['petty.cash.settlement'].create(settlement_vals)

            # Update computed fields
            self._compute_settlement_amount()
            self._set_settlement_status()

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Successfully imported %s settlement lines') % len(df),
                    'type': 'success',
                    'sticky': False,
                }
            }

        except Exception as e:
            raise UserError(_('Error importing Excel file: %s') % str(e))

