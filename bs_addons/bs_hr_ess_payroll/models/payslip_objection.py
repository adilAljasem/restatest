from odoo import api, fields, models
from odoo.exceptions import ValidationError


class BsHrPayslipObjection(models.Model):
    _name = 'bs.hr.payslip.objection'
    _description = 'Payslip Objection'
    _inherit = ['bs.hr.request.mixin']
    _ess_sequence_code = 'bs.hr.payslip.objection'
    _ess_auto_done = True

    payslip_id = fields.Many2one('hr.payslip', string='Payslip', required=True, index=True,
                                 ondelete='restrict', tracking=True)
    payslip_ref = fields.Char(compute='_compute_payslip_info', string='Payslip reference')
    payslip_period = fields.Char(compute='_compute_payslip_info', string='Period')
    line_id = fields.Many2one('hr.payslip.line', string='Contested line', ondelete='set null',
                              domain="[('slip_id', '=', payslip_id)]")
    line_amount = fields.Float(compute='_compute_payslip_info', string='Line amount')
    currency_id = fields.Many2one(related='company_id.currency_id')
    claimed_amount = fields.Monetary(string='Claimed amount', tracking=True,
                                     help='Amount the employee believes is missing or wrongly deducted.')
    reason = fields.Text(required=True)
    resolution_type = fields.Selection([
        ('addition', 'Pay to the employee (addition)'),
        ('deduction', 'Recover from the employee (deduction)'),
    ], string='Resolution', default='addition', tracking=True)
    resolution_amount = fields.Monetary(string='Accepted amount', tracking=True,
                                        help='Amount of the correction added to the next payslip. '
                                             '0 = the objection is accepted without any amount.')
    resolution_note = fields.Text(string='Resolution note')
    adjustment_id = fields.Many2one('bs.hr.payroll.adjustment', string='Adjustment', readonly=True, copy=False)
    adjustment_state = fields.Selection(related='adjustment_id.state', string='Adjustment status')

    @api.depends('payslip_id', 'line_id')
    def _compute_payslip_info(self):
        compat = self.env['bs.hr.payroll.compat']
        for rec in self:
            slip = rec.payslip_id.sudo()
            rec.payslip_ref = compat._slip_reference(slip) if slip else False
            rec.payslip_period = '%s - %s' % (slip.date_from, slip.date_to) if slip else False
            rec.line_amount = rec.line_id.sudo().total if rec.line_id else 0.0

    @api.constrains('payslip_id', 'employee_id', 'line_id')
    def _check_payslip(self):
        for rec in self:
            slip = rec.payslip_id.sudo()
            if slip.employee_id != rec.employee_id:
                raise ValidationError(self.env._('The payslip must belong to the employee.'))
            if rec.line_id and rec.line_id.sudo().slip_id != slip:
                raise ValidationError(self.env._('The contested line must belong to the payslip.'))

    @api.constrains('claimed_amount', 'resolution_amount')
    def _check_amounts(self):
        for rec in self:
            if rec.claimed_amount < 0 or rec.resolution_amount < 0:
                raise ValidationError(self.env._('Amounts must be positive.'))

    # ------------------------------------------------------------------
    # request hooks
    # ------------------------------------------------------------------
    def _ess_check_submit(self):
        for rec in self:
            if not rec.payslip_id.sudo()._ess_portal_visible():
                raise ValidationError(self.env._('This payslip cannot be contested.'))
            if not (rec.reason or '').strip():
                raise ValidationError(self.env._('Please explain the reason of the objection.'))

    def _ess_portal_summary(self):
        self.ensure_one()
        label = self.line_id.sudo().name if self.line_id else self.payslip_ref
        return '%s - %s' % (label or '', (self.reason or '')[:80])

    def _ess_portal_detail_fields(self):
        return ['payslip_ref', 'payslip_period', 'line_id', 'line_amount', 'claimed_amount', 'reason',
                'resolution_type', 'resolution_amount', 'resolution_note']

    def _ess_portal_approval_fields(self):
        return ['resolution_amount', 'resolution_type', 'resolution_note']

    def _ess_portal_url(self):
        self.ensure_one()
        return '/my/hr/request/%s/%s' % (self._name, self.id)

    def _ess_on_approved(self):
        Adjustment = self.env['bs.hr.payroll.adjustment']
        for rec in self:
            if not rec.resolution_amount:
                continue
            slip = rec.payslip_id.sudo()
            label = self.env._('Payslip objection %(name)s (%(ref)s)', name=rec.name,
                               ref=self.env['bs.hr.payroll.compat']._slip_reference(slip))
            adjustment = Adjustment._ess_create_from(
                rec, rec.employee_id, rec.resolution_amount, label, 'objection',
                adj_type=rec.resolution_type or 'addition')
            rec.sudo().adjustment_id = adjustment

    def _ess_on_cancelled(self):
        for rec in self.filtered(lambda r: r.adjustment_id.state == 'pending'):
            rec.adjustment_id.sudo().action_cancel()
