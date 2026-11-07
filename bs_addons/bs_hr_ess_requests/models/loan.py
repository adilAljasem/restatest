from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_round

OPEN_LOAN_STATES = ('submitted', 'in_approval', 'approved')
OPEN_LINE_STATES = ('pending', 'postponed')


def _default_first_installment():
    today = fields.Date.today()
    return today.replace(day=1) + relativedelta(months=1)


class BsHrLoanType(models.Model):
    _name = 'bs.hr.loan.type'
    _description = 'ESS Loan Type'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company,
                                 help='Leave empty to use the type in all companies.')
    max_percent_wage = fields.Float(
        string='Max % of monthly wage', default=100.0,
        help='Maximum amount as a percentage of the monthly wage (0 = no limit).')
    max_amount = fields.Float(string='Max amount', help='Absolute maximum amount (0 = no limit).')
    max_installments = fields.Integer(string='Max installments', default=6)
    min_service_months = fields.Integer(string='Minimum service (months)', default=3)
    allow_parallel = fields.Boolean(
        string='Allow parallel loans',
        help='When unchecked, an employee cannot request a new loan while another one is still open.')
    account_id = fields.Many2one(
        'account.account', string='Loan account (debit)',
        help='Employee receivable account debited when the loan is disbursed.')
    journal_id = fields.Many2one(
        'account.journal', string='Disbursement journal',
        help='The default account of this journal is credited when the loan is disbursed.')
    description = fields.Text(translate=True)

    _max_installments_positive = models.Constraint(
        'CHECK(max_installments > 0)', 'The maximum number of installments must be positive.')

    def _ess_limits(self, employee):
        """Return the limits of this loan type for ``employee``.

        :returns: dict(max_amount, max_installments, service_months, eligible, reasons)
        """
        self.ensure_one()
        employee = employee.sudo()
        wage = employee._ess_monthly_wage()
        candidates = []
        if self.max_percent_wage:
            candidates.append(wage * self.max_percent_wage / 100.0)
        if self.max_amount:
            candidates.append(self.max_amount)
        max_amount = float_round(min(candidates), precision_digits=2) if candidates else 0.0
        start = employee._ess_service_start()
        today = fields.Date.context_today(self)
        service_months = 0
        if start:
            delta = relativedelta(today, start)
            service_months = delta.years * 12 + delta.months
        reasons = []
        if self.min_service_months and service_months < self.min_service_months:
            reasons.append(self.env._('A minimum of %(months)s months of service is required (you have %(have)s).',
                                      months=self.min_service_months, have=service_months))
        if not candidates:
            max_amount = 0.0
            reasons.append(self.env._('No limit is configured on this loan type.'))
        elif max_amount <= 0:
            reasons.append(self.env._('No wage is registered on your contract.'))
        if not self.allow_parallel:
            open_loan = self.env['bs.hr.loan'].sudo().search_count([
                ('employee_id', '=', employee.id), ('state', 'in', OPEN_LOAN_STATES)])
            if open_loan:
                reasons.append(self.env._('You already have an open loan.'))
        return {
            'max_amount': max_amount,
            'max_installments': self.max_installments,
            'service_months': service_months,
            'eligible': not reasons,
            'reasons': reasons,
        }

    @api.model
    def _ess_schedule(self, amount, count, first_date):
        """Split ``amount`` into ``count`` monthly installments (last one absorbs rounding)."""
        if not amount or not count or count <= 0:
            return []
        base = float_round(amount / count, precision_digits=2)
        result = []
        for i in range(count):
            value = base if i < count - 1 else float_round(amount - base * (count - 1), precision_digits=2)
            result.append((first_date + relativedelta(months=i), value))
        return result


class BsHrLoan(models.Model):
    _name = 'bs.hr.loan'
    _description = 'Loan'
    _inherit = ['bs.hr.request.mixin']
    _ess_sequence_code = 'bs.hr.loan'

    loan_type_id = fields.Many2one('bs.hr.loan.type', string='Loan type', required=True, tracking=True,
                                   domain="[('company_id', 'in', [company_id, False])]")
    currency_id = fields.Many2one(related='company_id.currency_id')
    amount = fields.Monetary(required=True, tracking=True)
    installment_count = fields.Integer(string='Installments', required=True, default=1, tracking=True)
    installment_amount = fields.Monetary(compute='_compute_installment_amount', string='Monthly installment')
    first_installment_date = fields.Date(required=True, default=lambda self: _default_first_installment())
    line_ids = fields.One2many('bs.hr.loan.line', 'loan_id', string='Installments schedule', copy=False)
    paid_amount = fields.Monetary(compute='_compute_balance', store=True)
    remaining_amount = fields.Monetary(compute='_compute_balance', store=True)
    move_id = fields.Many2one('account.move', string='Disbursement entry', readonly=True, copy=False)
    disbursed = fields.Boolean(readonly=True, copy=False, tracking=True)
    disburse_date = fields.Date(readonly=True, copy=False)
    postpone_ids = fields.One2many('bs.hr.loan.postpone', 'loan_id', string='Postponements')

    _amount_positive = models.Constraint('CHECK(amount > 0)', 'The loan amount must be positive.')
    _installments_positive = models.Constraint('CHECK(installment_count > 0)',
                                               'The number of installments must be positive.')

    @api.depends('amount', 'installment_count')
    def _compute_installment_amount(self):
        for loan in self:
            loan.installment_amount = loan.amount / loan.installment_count if loan.installment_count else 0.0

    @api.depends('amount', 'state', 'line_ids.state', 'line_ids.amount')
    def _compute_balance(self):
        for loan in self:
            paid = sum(loan.line_ids.filtered(lambda l: l.state == 'paid').mapped('amount'))
            loan.paid_amount = paid
            if loan.line_ids:
                loan.remaining_amount = sum(loan.line_ids.filtered(
                    lambda l: l.state in OPEN_LINE_STATES).mapped('amount'))
            else:
                loan.remaining_amount = loan.amount if loan.state in ('approved',) else 0.0

    # ------------------------------------------------------------------
    # request hooks
    # ------------------------------------------------------------------
    def _ess_check_submit(self):
        for loan in self:
            limits = loan.loan_type_id._ess_limits(loan.employee_id)
            if not limits['eligible']:
                raise ValidationError('\n'.join(limits['reasons']))
            if loan.amount > limits['max_amount']:
                raise ValidationError(self.env._(
                    'The maximum amount you can request is %(max)s.',
                    max='{:,.2f}'.format(limits['max_amount'])))
            if loan.installment_count > limits['max_installments']:
                raise ValidationError(self.env._(
                    'The maximum number of installments is %s.', limits['max_installments']))
            if loan.first_installment_date and loan.first_installment_date < fields.Date.context_today(loan):
                raise ValidationError(self.env._('The first installment cannot be in the past.'))

    def _ess_on_approved(self):
        for loan in self:
            loan._ess_generate_schedule()

    def _ess_on_cancelled(self):
        for loan in self:
            adjustments = loan.line_ids.adjustment_id.filtered(lambda a: a.state == 'pending')
            adjustments.sudo().write({'state': 'cancelled'})
            loan.line_ids.filtered(lambda l: l.state in OPEN_LINE_STATES).write({'state': 'cancelled'})
            if loan.move_id and loan.move_id.state == 'posted':
                loan.message_post(body=self.env._(
                    'The disbursement entry %s is posted: reverse it manually if needed.', loan.move_id.name))

    def _ess_portal_summary(self):
        self.ensure_one()
        return '%s · %s %s · %s x %s' % (
            self.loan_type_id.name, '{:,.2f}'.format(self.amount), self.currency_id.symbol or '',
            self.installment_count, '{:,.2f}'.format(self.installment_amount))

    def _ess_portal_url(self):
        self.ensure_one()
        return '/my/hr/loan/%s' % self.id

    def _ess_portal_detail_fields(self):
        return ['loan_type_id', 'amount', 'installment_count', 'installment_amount',
                'first_installment_date', 'remaining_amount']

    # ------------------------------------------------------------------
    # business
    # ------------------------------------------------------------------
    def _ess_generate_schedule(self):
        for loan in self:
            if loan.line_ids:
                continue
            schedule = self.env['bs.hr.loan.type']._ess_schedule(
                loan.amount, loan.installment_count, loan.first_installment_date)
            loan.sudo().write({'line_ids': [(0, 0, {'date': d, 'amount': a}) for d, a in schedule]})

    def action_generate_schedule(self):
        self.filtered(lambda l: l.state == 'approved')._ess_generate_schedule()
        return True

    def action_disburse(self):
        for loan in self:
            if loan.state != 'approved':
                raise UserError(self.env._('Only approved loans can be disbursed.'))
            if loan.disbursed:
                raise UserError(self.env._('The loan %s is already disbursed.', loan.name))
            loan._ess_generate_schedule()
            loan._ess_create_disbursement_move()
            Adjustment = self.env['bs.hr.payroll.adjustment']
            for line in loan.line_ids.filtered(lambda l: l.state in OPEN_LINE_STATES and not l.adjustment_id):
                line.adjustment_id = Adjustment._ess_create_from(
                    line, loan.employee_id, line.amount,
                    self.env._('Loan %(loan)s - installment %(date)s', loan=loan.name, date=line.date),
                    'loan', date=line.date)
            loan.write({'disbursed': True, 'disburse_date': fields.Date.context_today(loan)})
        return True

    def _ess_create_disbursement_move(self):
        self.ensure_one()
        # accounting configuration is read in sudo: HR officers disburse without accounting rights
        loan_type = self.loan_type_id.sudo()
        journal = loan_type.journal_id
        if not journal or not loan_type.account_id:
            raise UserError(self.env._(
                'Configure the loan account and the disbursement journal on the loan type %s.', loan_type.name))
        credit_account = journal.default_account_id
        if not credit_account:
            raise UserError(self.env._('The journal %s has no default account.', journal.name))
        partner = self.employee_id.sudo().work_contact_id
        label = self.env._('Loan %(loan)s - %(employee)s', loan=self.name, employee=self.employee_id.name)
        move = self.env['account.move'].sudo().create({
            'move_type': 'entry',
            'journal_id': journal.id,
            'date': fields.Date.context_today(self),
            'ref': self.name,
            'company_id': self.company_id.id,
            'line_ids': [
                (0, 0, {'name': label, 'account_id': loan_type.account_id.id, 'partner_id': partner.id,
                        'debit': self.amount, 'credit': 0.0}),
                (0, 0, {'name': label, 'account_id': credit_account.id, 'partner_id': partner.id,
                        'debit': 0.0, 'credit': self.amount}),
            ],
        })
        move.action_post()
        self.sudo().move_id = move
        return move

    def _ess_check_done(self):
        for loan in self:
            if loan.state == 'approved' and loan.disbursed and loan.line_ids \
                    and all(l.state in ('paid', 'cancelled') for l in loan.line_ids):
                loan.state = 'done'
                loan._ess_notify_employee(
                    loan._ess_lang_env()._('Loan fully paid'),
                    loan._ess_lang_env()._('Your loan %s is fully paid.', loan.name))

    def _ess_sync_lines(self):
        """Update installment states from their payroll adjustments."""
        for line in self.line_ids:
            adj = line.adjustment_id
            if not adj:
                continue
            if adj.state == 'applied' and line.state != 'paid':
                line.state = 'paid'
            elif adj.state == 'cancelled' and line.state in OPEN_LINE_STATES:
                line.state = 'cancelled'
            elif adj.state == 'pending' and line.state in ('paid', 'cancelled'):
                line.state = 'pending'
        self._ess_check_done()

    @api.model
    def _cron_sync_loans(self):
        loans = self.sudo().search([('state', '=', 'approved'), ('disbursed', '=', True)])
        loans._ess_sync_lines()

    def action_view_move(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'res_id': self.move_id.id,
                'view_mode': 'form'}


class BsHrLoanLine(models.Model):
    _name = 'bs.hr.loan.line'
    _description = 'ESS Loan Installment'
    _order = 'date, id'

    loan_id = fields.Many2one('bs.hr.loan', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='loan_id.employee_id', store=True)
    company_id = fields.Many2one(related='loan_id.company_id', store=True)
    currency_id = fields.Many2one(related='loan_id.currency_id')
    date = fields.Date(required=True)
    amount = fields.Monetary(required=True)
    state = fields.Selection([
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('postponed', 'Postponed'),
        ('cancelled', 'Cancelled'),
    ], default='pending', required=True, index=True)
    adjustment_id = fields.Many2one('bs.hr.payroll.adjustment', readonly=True, copy=False, ondelete='set null')

    def _compute_display_name(self):
        for line in self:
            line.display_name = '%s - %s' % (line.loan_id.name or '', line.date or '')


class BsHrLoanPostpone(models.Model):
    _name = 'bs.hr.loan.postpone'
    _description = 'Installment Postponement'
    _inherit = ['bs.hr.request.mixin']
    _ess_sequence_code = 'bs.hr.loan.postpone'
    _ess_auto_done = True

    loan_line_id = fields.Many2one('bs.hr.loan.line', string='Installment', required=True,
                                   domain="[('employee_id', '=', employee_id), ('state', 'in', ('pending', 'postponed'))]")
    loan_id = fields.Many2one(related='loan_line_id.loan_id', store=True, string='Loan')
    currency_id = fields.Many2one(related='loan_line_id.currency_id')
    installment_date = fields.Date(related='loan_line_id.date', string='Installment date')
    installment_amount = fields.Monetary(related='loan_line_id.amount', string='Installment amount')
    reason = fields.Text(required=True)
    new_date = fields.Date(readonly=True, copy=False)

    @api.constrains('loan_line_id', 'employee_id')
    def _check_line_employee(self):
        for rec in self:
            if rec.loan_line_id.loan_id.employee_id != rec.employee_id:
                raise ValidationError(self.env._('The installment does not belong to this employee.'))

    def _ess_check_submit(self):
        for rec in self:
            line = rec.loan_line_id
            if line.state not in OPEN_LINE_STATES or line.loan_id.state != 'approved':
                raise ValidationError(self.env._('Only unpaid installments can be postponed.'))
            if line.adjustment_id and line.adjustment_id.state != 'pending':
                raise ValidationError(self.env._('This installment is already processed by payroll.'))
            other = self.sudo().search_count([
                ('id', '!=', rec.id), ('loan_line_id', '=', line.id),
                ('state', 'in', ('submitted', 'in_approval'))])
            if other:
                raise ValidationError(self.env._('A postponement is already pending for this installment.'))

    def _ess_on_approved(self):
        for rec in self:
            line = rec.loan_line_id.sudo()
            last = max(line.loan_id.line_ids.mapped('date'))
            new_date = last + relativedelta(months=1)
            line.write({'date': new_date, 'state': 'postponed'})
            if line.adjustment_id and line.adjustment_id.state == 'pending':
                line.adjustment_id.date = new_date
            rec.new_date = new_date
            line.loan_id.message_post(body=self.env._(
                'Installment postponed to %(date)s (%(ref)s).', date=new_date, ref=rec.name))

    def _ess_portal_summary(self):
        self.ensure_one()
        return '%s · %s · %s' % (self.loan_id.name or '', self.installment_date or '',
                                 '{:,.2f}'.format(self.installment_amount))

    def _ess_portal_detail_fields(self):
        return ['loan_id', 'installment_date', 'installment_amount', 'reason', 'new_date']


class BsHrPayrollAdjustment(models.Model):
    _inherit = 'bs.hr.payroll.adjustment'

    def write(self, vals):
        res = super().write(vals)
        if 'state' in vals:
            lines = self.env['bs.hr.loan.line'].sudo().search([('adjustment_id', 'in', self.ids)])
            if lines:
                lines.loan_id._ess_sync_lines()
        return res
