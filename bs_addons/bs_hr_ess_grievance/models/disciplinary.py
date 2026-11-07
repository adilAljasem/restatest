from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError

PENALTY_ACTIONS = [
    ('verbal_warning', 'Verbal warning'),
    ('written_warning', 'Written warning'),
    ('deduction_days', 'Deduction (days of wage)'),
    ('deduction_amount', 'Deduction (fixed amount)'),
    ('suspension', 'Suspension'),
    ('termination', 'Termination'),
]
LOOKBACK_DAYS = 180


class BsHrViolationType(models.Model):
    """Entry of the company's penalty regulation (لائحة الجزاءات)."""
    _name = 'bs.hr.violation.type'
    _description = 'Violation type'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', help='Leave empty to share it between companies.')
    description = fields.Text(translate=True)
    first_action = fields.Selection(PENALTY_ACTIONS, string='1st occurrence', required=True,
                                    default='written_warning')
    first_value = fields.Float(string='1st value', help='Days or amount for a deduction.')
    second_action = fields.Selection(PENALTY_ACTIONS, string='2nd occurrence', required=True,
                                     default='deduction_days')
    second_value = fields.Float(string='2nd value', default=1.0)
    third_action = fields.Selection(PENALTY_ACTIONS, string='3rd occurrence and more', required=True,
                                    default='deduction_days')
    third_value = fields.Float(string='3rd value', default=2.0)
    deduction_value = fields.Float(
        string='Default deduction', default=1.0,
        help='Days (deduction in days) or amount (fixed deduction) used when the step has no value.')

    _code_uniq = models.Constraint('UNIQUE(code, company_id)', 'The code must be unique per company.')

    def _ladder_step(self, occurrence):
        """(action, value) for the given occurrence (1, 2, 3+)."""
        self.ensure_one()
        if occurrence <= 1:
            action, value = self.first_action, self.first_value
        elif occurrence == 2:
            action, value = self.second_action, self.second_value
        else:
            action, value = self.third_action, self.third_value
        if action in ('deduction_days', 'deduction_amount', 'suspension') and not value:
            value = self.deduction_value
        return action, value


class BsHrDisciplinary(models.Model):
    _name = 'bs.hr.disciplinary'
    _description = 'Disciplinary action'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(string='Reference', default='/', readonly=True, copy=False)
    employee_id = fields.Many2one('hr.employee', required=True, index=True, tracking=True, check_company=True)
    company_id = fields.Many2one('res.company', related='employee_id.company_id', store=True, index=True)
    work_location_id = fields.Many2one(related='employee_id.work_location_id', store=True, string='Branch')
    currency_id = fields.Many2one(related='company_id.currency_id')
    violation_type_id = fields.Many2one('bs.hr.violation.type', required=True, tracking=True,
                                        domain="[('company_id', 'in', [company_id, False])]")
    date = fields.Date(string='Violation date', required=True, default=fields.Date.context_today, index=True,
                       tracking=True)
    description = fields.Text(required=True)
    occurrence = fields.Integer(compute='_compute_occurrence', store=True, readonly=False, precompute=True,
                                help='Confirmed violations of the same type in the last 180 days + 1.')
    action = fields.Selection(PENALTY_ACTIONS, compute='_compute_action', store=True, readonly=False,
                              required=True, tracking=True, precompute=True)
    deduction_value = fields.Float(compute='_compute_action', store=True, readonly=False, precompute=True,
                                   string='Days / amount')
    deduction_amount = fields.Monetary(compute='_compute_deduction_amount', store=True, readonly=False,
                                       tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('notified', 'Notified'),
        ('appealed', 'Appealed'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, index=True, copy=False)
    notified_date = fields.Datetime(readonly=True, copy=False)
    appeal_deadline = fields.Date(readonly=True, copy=False)
    employee_ack_date = fields.Datetime(string='Acknowledged by the employee', readonly=True, copy=False)
    grievance_id = fields.Many2one('bs.hr.grievance', string='Appeal', readonly=True, copy=False,
                                   groups='bs_hr_ess_base.group_ess_grievance_committee')
    adjustment_id = fields.Many2one('bs.hr.payroll.adjustment', readonly=True, copy=False)

    # ------------------------------------------------------------------
    # computes
    # ------------------------------------------------------------------
    @api.depends('employee_id', 'violation_type_id', 'date')
    def _compute_occurrence(self):
        for rec in self:
            if not (rec.employee_id and rec.violation_type_id and rec.date):
                rec.occurrence = 1
                continue
            domain = [
                ('employee_id', '=', rec.employee_id.id),
                ('violation_type_id', '=', rec.violation_type_id.id),
                ('state', '=', 'confirmed'),
                ('date', '>=', rec.date - timedelta(days=LOOKBACK_DAYS)),
                ('date', '<=', rec.date),
            ]
            if rec.id:
                domain.append(('id', '!=', rec.id))
            rec.occurrence = self.sudo().search_count(domain) + 1

    @api.depends('violation_type_id', 'occurrence')
    def _compute_action(self):
        for rec in self:
            if rec.violation_type_id:
                rec.action, rec.deduction_value = rec.violation_type_id._ladder_step(rec.occurrence or 1)
            else:
                rec.action = rec.action or 'verbal_warning'
                rec.deduction_value = rec.deduction_value or 0.0

    @api.depends('action', 'deduction_value', 'employee_id')
    def _compute_deduction_amount(self):
        for rec in self:
            if rec.action == 'deduction_days' and rec.employee_id:
                rec.deduction_amount = rec.company_id.currency_id.round(
                    rec.employee_id._ess_monthly_wage() / 30.0 * rec.deduction_value)
            elif rec.action == 'deduction_amount':
                rec.deduction_amount = rec.deduction_value
            else:
                rec.deduction_amount = 0.0

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].sudo().next_by_code('bs.hr.disciplinary') or '/'
        return super().create(vals_list)

    def unlink(self):
        if any(rec.state not in ('draft', 'cancelled') for rec in self):
            raise UserError(self.env._('Only draft or cancelled disciplinary actions can be deleted.'))
        return super().unlink()

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def action_notify_employee(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.state != 'draft':
                raise UserError(self.env._('Only draft actions can be notified.'))
            days = rec.company_id.ess_appeal_days or 15
            rec.write({'state': 'notified', 'notified_date': now,
                       'appeal_deadline': fields.Date.context_today(rec) + timedelta(days=days)})
            rec._ess_notify(rec._ess_lang_env()._(
                'A disciplinary action (%(action)s) has been issued. You may appeal until %(date)s.',
                action=rec._action_label(), date=rec.appeal_deadline))
        return True

    def action_confirm(self):
        for rec in self:
            if rec.state not in ('draft', 'notified'):
                raise UserError(self.env._('Only draft or notified actions can be confirmed '
                                           '(an appealed action waits for the committee decision).'))
            rec.state = 'confirmed'
            if rec.action in ('deduction_days', 'deduction_amount') and rec.deduction_amount > 0 \
                    and not rec.adjustment_id:
                label = self.env._('%(ref)s - %(type)s', ref=rec.name, type=rec.violation_type_id.name)
                rec.adjustment_id = self.env['bs.hr.payroll.adjustment']._ess_create_from(
                    rec, rec.employee_id, rec.deduction_amount, label, 'disciplinary')
            rec._ess_notify(rec._ess_lang_env()._('The disciplinary action %s is now final.', rec.name))
        return True

    def action_cancel(self):
        for rec in self:
            if rec.adjustment_id:
                if rec.adjustment_id.state == 'applied':
                    raise UserError(self.env._('The deduction of %s has already been applied on a payslip.',
                                               rec.name))
                rec.adjustment_id.sudo().action_cancel()
            rec.state = 'cancelled'
        return True

    def action_reset_draft(self):
        self.filtered(lambda r: r.state == 'cancelled').write(
            {'state': 'draft', 'notified_date': False, 'appeal_deadline': False, 'adjustment_id': False})
        return True

    # ------------------------------------------------------------------
    # employee side
    # ------------------------------------------------------------------
    def _action_label(self):
        self.ensure_one()
        return dict(self._fields['action']._description_selection(self.env))[self.action]

    def _ess_lang_env(self):
        self.ensure_one()
        lang = self.employee_id.sudo().user_id.lang or self.env.lang
        return self.with_context(lang=lang).env

    def _ess_notify(self, body):
        for rec in self:
            self.env['bs.hr.notification'].sudo().create({
                'employee_id': rec.employee_id.id, 'title': '%s - %s' % (rec.name, rec.violation_type_id.name),
                'body': body, 'url': '/my/hr/disciplinary', 'res_model': rec._name, 'res_id': rec.id,
            })
            partner = rec.employee_id.sudo().user_id.partner_id
            if partner:
                rec.sudo().message_notify(partner_ids=partner.ids, subject=rec.name, body=body)

    def _ess_can_appeal(self, today=None):
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        return self.state == 'notified' and bool(self.appeal_deadline) and today <= self.appeal_deadline

    def _ess_acknowledge(self):
        for rec in self:
            if rec.state in ('notified', 'appealed', 'confirmed') and not rec.employee_ack_date:
                rec.employee_ack_date = fields.Datetime.now()
                rec.message_post(body=self.env._('Acknowledged by the employee from the portal.'))
        return True

    def _ess_appeal(self, reason):
        """Create the level-1 appeal grievance (never anonymous) and freeze the action."""
        self.ensure_one()
        if not self._ess_can_appeal():
            raise UserError(self.env._('The appeal period of this disciplinary action is over.'))
        if not (reason or '').strip():
            raise UserError(self.env._('Please explain why you appeal this disciplinary action.'))
        grievance = self.env['bs.hr.grievance']._ess_create_for_employee(self.employee_id, {
            'grievance_type': 'appeal_disciplinary',
            'subject': self.env._('Appeal against %(ref)s (%(type)s)', ref=self.name,
                                  type=self.violation_type_id.name),
            'description': reason.strip(),
            'related_res_model': self._name,
            'related_res_id': self.id,
        })
        self.sudo().write({'state': 'appealed', 'grievance_id': grievance.id})
        self.sudo().message_post(body=Markup('%s <b>%s</b>') % (
            self.env._('The employee appealed this action. Case:'), grievance.name))
        return grievance

    def _ess_grievance_parties(self):
        """Users who must not handle an appeal against this action."""
        self.ensure_one()
        employee = self.employee_id.sudo()
        return self.create_uid | employee.parent_id.user_id | employee.work_location_id.ess_manager_id

    def _ess_on_grievance_decision(self, grievance, decision):
        """Committee decision on the appeal: accepted (resolved) cancels the action,
        rejected puts it back to "notified" for HR to confirm."""
        for rec in self.sudo():
            if rec.state != 'appealed':
                continue
            if decision == 'resolved':
                rec.action_cancel()
                rec.message_post(body=self.env._('Appeal accepted by the committee: action cancelled.'))
            else:
                rec.state = 'notified'
                rec.message_post(body=self.env._('Appeal rejected by the committee: the action can be confirmed.'))
                if rec.create_uid and not rec.create_uid.share:
                    rec.activity_schedule('mail.mail_activity_data_todo', user_id=rec.create_uid.id,
                                          summary=self.env._('Confirm %s (appeal rejected)', rec.name))
