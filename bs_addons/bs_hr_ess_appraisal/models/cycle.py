from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain


class BsHrAppraisalCycle(models.Model):
    _name = 'bs.hr.appraisal.cycle'
    _description = 'Appraisal Cycle'
    _inherit = ['mail.thread']
    _order = 'date_from desc, id desc'

    name = fields.Char(required=True, tracking=True)
    cycle_type = fields.Selection([
        ('annual', 'Annual'),
        ('semi_annual', 'Semi-annual'),
        ('probation', 'End of probation'),
    ], required=True, default='annual', tracking=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    self_review_deadline = fields.Date()
    template_id = fields.Many2one('bs.hr.appraisal.template', required=True,
                                  domain="[('company_id', 'in', (company_id, False))]")
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    selection_mode = fields.Selection([
        ('all', 'All employees'),
        ('branches', 'Branches'),
        ('departments', 'Departments'),
    ], string='Employees', required=True, default='all')
    work_location_ids = fields.Many2many('hr.work.location', string='Branches')
    department_ids = fields.Many2many('hr.department', string='Departments')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('open', 'Open'),
        ('closed', 'Closed'),
    ], default='draft', required=True, tracking=True)
    appraisal_ids = fields.One2many('bs.hr.ess.appraisal', 'cycle_id')
    appraisal_count = fields.Integer(compute='_compute_appraisal_count')

    @api.depends('appraisal_ids')
    def _compute_appraisal_count(self):
        data = dict(self.env['bs.hr.ess.appraisal']._read_group(
            [('cycle_id', 'in', self.ids)], ['cycle_id'], ['__count']))
        for cycle in self:
            cycle.appraisal_count = data.get(cycle, 0)

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for cycle in self:
            if cycle.date_from > cycle.date_to:
                raise ValidationError(self.env._('The cycle start date must be before its end date.'))

    def _target_employees(self):
        self.ensure_one()
        domain = Domain('company_id', '=', self.company_id.id)
        if self.selection_mode == 'branches':
            domain &= Domain('work_location_id', 'in', self.work_location_ids.ids)
        elif self.selection_mode == 'departments':
            domain &= Domain('department_id', 'child_of', self.department_ids.ids)
        return self.env['hr.employee'].sudo().search(domain)

    def action_generate_appraisals(self):
        Appraisal = self.env['bs.hr.ess.appraisal'].sudo()
        created = Appraisal
        for cycle in self:
            if cycle.state == 'closed':
                raise UserError(self.env._('The cycle %s is closed.', cycle.name))
            if cycle.selection_mode == 'branches' and not cycle.work_location_ids:
                raise UserError(self.env._('Select at least one branch.'))
            if cycle.selection_mode == 'departments' and not cycle.department_ids:
                raise UserError(self.env._('Select at least one department.'))
            existing = Appraisal.search([('cycle_id', '=', cycle.id)]).employee_id
            employees = cycle._target_employees() - existing
            if employees:
                created |= Appraisal.create([cycle._appraisal_vals(emp) for emp in employees])
            if cycle.state == 'draft':
                cycle.state = 'open'
        created._ess_notify_self_review()
        if len(self) == 1:
            return self.action_view_appraisals()
        return True

    def _appraisal_vals(self, employee):
        self.ensure_one()
        return {
            'cycle_id': self.id,
            'employee_id': employee.id,
            'manager_id': employee.parent_id.id,
            'template_id': self.template_id.id,
            'date_from': self.date_from,
            'date_to': self.date_to,
            'deadline': self.self_review_deadline,
            'company_id': employee.company_id.id,
        }

    def action_open(self):
        self.write({'state': 'open'})

    def action_close(self):
        self.write({'state': 'closed'})

    def action_reset_draft(self):
        self.write({'state': 'draft'})

    def action_view_appraisals(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('bs_hr_ess_appraisal.action_appraisal')
        action['domain'] = [('cycle_id', '=', self.id)]
        action['context'] = {'default_cycle_id': self.id}
        return action
