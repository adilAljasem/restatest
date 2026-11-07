from odoo import api, fields, models
from odoo.exceptions import UserError

ADJ_CATEGORIES = [
    ('loan', 'Loan installment'),
    ('custody', 'Custody (lost / damaged)'),
    ('disciplinary', 'Disciplinary penalty'),
    ('objection', 'Payslip objection'),
    ('attendance', 'Attendance'),
    ('allowance', 'Allowance'),
    ('other', 'Other'),
]


class BsHrPayrollAdjustment(models.Model):
    """Single ledger of every amount that must hit a payslip.

    Every module (loans, custody, penalties, objections) writes here, the
    payroll bridge reads pending lines and turns them into payslip inputs.
    This gives full traceability from a payslip line back to its source.
    """
    _name = 'bs.hr.payroll.adjustment'
    _description = 'ESS Payroll Adjustment'
    _inherit = ['mail.thread']
    _order = 'date, id'

    name = fields.Char(string='Description', required=True)
    employee_id = fields.Many2one('hr.employee', required=True, index=True, tracking=True)
    company_id = fields.Many2one('res.company', related='employee_id.company_id', store=True, index=True)
    work_location_id = fields.Many2one(related='employee_id.work_location_id', store=True, string='Branch')
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date(string='Payroll date', required=True, default=fields.Date.context_today, index=True,
                       help='The adjustment is picked up by the first payslip ending on or after this date.')
    adj_type = fields.Selection([('deduction', 'Deduction'), ('addition', 'Addition')],
                                required=True, default='deduction', string='Type')
    category = fields.Selection(ADJ_CATEGORIES, required=True, default='other', index=True)
    amount = fields.Monetary(required=True, tracking=True)
    signed_amount = fields.Monetary(compute='_compute_signed_amount', store=True)
    state = fields.Selection([
        ('pending', 'Pending'),
        ('applied', 'Applied'),
        ('cancelled', 'Cancelled'),
    ], default='pending', required=True, tracking=True, index=True)
    applied_date = fields.Date(readonly=True)
    source_model = fields.Char(readonly=True, index=True)
    source_id = fields.Many2oneReference(model_field='source_model', readonly=True, string='Source ID')
    source_ref = fields.Char(compute='_compute_source_ref', string='Source')
    note = fields.Text()

    _amount_positive = models.Constraint('CHECK(amount >= 0)', 'The amount must be positive.')

    @api.depends('amount', 'adj_type')
    def _compute_signed_amount(self):
        for rec in self:
            rec.signed_amount = -rec.amount if rec.adj_type == 'deduction' else rec.amount

    @api.depends('source_model', 'source_id')
    def _compute_source_ref(self):
        for rec in self:
            ref = ''
            if rec.source_model and rec.source_id and rec.source_model in self.env:
                src = self.env[rec.source_model].sudo().browse(rec.source_id).exists()
                ref = src.display_name if src else ''
            rec.source_ref = ref

    def action_open_source(self):
        self.ensure_one()
        if not (self.source_model and self.source_id):
            return False
        return {'type': 'ir.actions.act_window', 'res_model': self.source_model,
                'res_id': self.source_id, 'view_mode': 'form'}

    def action_mark_applied(self):
        self.filtered(lambda a: a.state == 'pending').write(
            {'state': 'applied', 'applied_date': fields.Date.context_today(self)})

    def action_cancel(self):
        if any(a.state == 'applied' for a in self):
            raise UserError(self.env._('Applied adjustments cannot be cancelled.'))
        self.write({'state': 'cancelled'})

    def action_reset_pending(self):
        self.write({'state': 'pending', 'applied_date': False})

    @api.model
    def _ess_create_from(self, source, employee, amount, name, category, date=None, adj_type='deduction'):
        return self.sudo().create({
            'employee_id': employee.id, 'amount': amount, 'name': name, 'category': category,
            'date': date or fields.Date.context_today(self), 'adj_type': adj_type,
            'source_model': source._name, 'source_id': source.id,
        })
