from datetime import timedelta

from odoo import api, fields, models
from odoo.fields import Domain
from odoo.tools.safe_eval import safe_eval

APPROVER_TYPES = [
    ('manager', 'Direct manager'),
    ('manager_2', "Manager's manager"),
    ('department', 'Department manager'),
    ('branch', 'Branch manager'),
    ('coach', 'Coach'),
    ('hr', 'HR officers'),
    ('group', 'Users of a group'),
    ('users', 'Specific users'),
]

LINE_STATES = [
    ('waiting', 'Waiting'),
    ('pending', 'Pending'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('skipped', 'Skipped'),
    ('cancelled', 'Cancelled'),
]


class BsHrApprovalFlow(models.Model):
    _name = 'bs.hr.approval.flow'
    _description = 'ESS Approval Flow'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', help='Leave empty to use the flow in all companies.')
    res_model = fields.Selection(selection='_selection_res_model', string='Request Type', required=True)
    condition_domain = fields.Char(
        string='Applies when', default='[]',
        help='Optional domain evaluated on the request: the first matching flow (by sequence) is used.')
    step_ids = fields.One2many('bs.hr.approval.flow.step', 'flow_id', string='Steps', copy=True)
    note = fields.Text(translate=True)

    @api.model
    def _selection_res_model(self):
        names = self.env.registry['bs.hr.request.mixin']._inherit_children
        result = []
        for name in sorted(names):
            model = self.env.get(name)
            if model is not None and not model._abstract and not model._transient:
                result.append((name, model._description))
        return result

    def _matches(self, request):
        self.ensure_one()
        domain = safe_eval(self.condition_domain or '[]')
        return not domain or bool(request.filtered_domain(domain))


class BsHrApprovalFlowStep(models.Model):
    _name = 'bs.hr.approval.flow.step'
    _description = 'ESS Approval Flow Step'
    _order = 'sequence, id'

    flow_id = fields.Many2one('bs.hr.approval.flow', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True)
    approver_type = fields.Selection(APPROVER_TYPES, required=True, default='manager')
    group_id = fields.Many2one('res.groups')
    user_ids = fields.Many2many('res.users', string='Approvers')
    condition_domain = fields.Char(
        string='Only when', default='[]',
        help='The step is skipped when the request does not match this domain '
             '(e.g. [("amount", ">", 5000)]).')
    sla_hours = fields.Integer(string='SLA (hours)', default=48,
                               help='0 = no deadline / no escalation.')
    escalation = fields.Selection([
        ('none', 'Remind only'),
        ('next', 'Move to next step'),
        ('users', 'Add escalation users'),
    ], default='users', required=True)
    escalation_user_ids = fields.Many2many(
        'res.users', 'bs_hr_flow_step_escalation_rel', 'step_id', 'user_id', string='Escalate to')

    def _resolve_approvers(self, request):
        """Return res.users who can approve ``request`` for this step."""
        self.ensure_one()
        employee = request.employee_id.sudo()
        users = self.env['res.users']
        kind = self.approver_type
        if kind == 'manager':
            users = employee.parent_id.user_id
        elif kind == 'manager_2':
            users = employee.parent_id.parent_id.user_id
        elif kind == 'department':
            users = employee.department_id.manager_id.user_id
        elif kind == 'branch':
            users = employee.work_location_id.ess_manager_id
        elif kind == 'coach':
            users = employee.coach_id.user_id
        elif kind == 'hr':
            users = self.env.ref('bs_hr_ess_base.group_ess_hr_officer').sudo().all_user_ids
        elif kind == 'group':
            users = self.group_id.sudo().all_user_ids
        elif kind == 'users':
            users = self.user_ids
        company = request.company_id
        users = users.filtered(lambda u: u.active and (not company or company in u.company_ids))
        # never let an employee approve their own request
        return users - employee.user_id

    def _applies_to(self, request):
        self.ensure_one()
        domain = safe_eval(self.condition_domain or '[]')
        return not domain or bool(request.filtered_domain(domain))


class BsHrApprovalLine(models.Model):
    _name = 'bs.hr.approval.line'
    _description = 'ESS Approval Line'
    _order = 'res_model, res_id, sequence, id'

    res_model = fields.Char(required=True, index=True)
    res_id = fields.Many2oneReference(model_field='res_model', required=True, index=True)
    request_name = fields.Char()
    employee_id = fields.Many2one('hr.employee', index=True)
    company_id = fields.Many2one('res.company', index=True)
    work_location_id = fields.Many2one('hr.work.location', index=True)
    sequence = fields.Integer()
    step_id = fields.Many2one('bs.hr.approval.flow.step', string='Flow step', ondelete='set null')
    name = fields.Char(string='Step', required=True)
    approver_ids = fields.Many2many('res.users', 'bs_hr_approval_line_user_rel', 'line_id', 'user_id',
                                    string='Approvers')
    state = fields.Selection(LINE_STATES, default='waiting', required=True, index=True)
    decided_by_id = fields.Many2one('res.users', string='Decided by')
    decided_on = fields.Datetime()
    comment = fields.Text()
    date_pending = fields.Datetime(string='Pending since')
    deadline = fields.Datetime(index=True)
    escalated = fields.Boolean()
    reminder_sent = fields.Boolean()
    duration_hours = fields.Float(compute='_compute_duration', store=True, aggregator='avg')
    sla_breached = fields.Boolean(compute='_compute_duration', store=True)

    @api.depends('date_pending', 'decided_on', 'deadline')
    def _compute_duration(self):
        for line in self:
            if line.date_pending and line.decided_on:
                line.duration_hours = (line.decided_on - line.date_pending).total_seconds() / 3600.0
            else:
                line.duration_hours = 0.0
            end = line.decided_on or fields.Datetime.now()
            line.sla_breached = bool(line.deadline and end > line.deadline)

    def _get_request(self):
        self.ensure_one()
        return self.env[self.res_model].browse(self.res_id).exists()

    def action_open_request(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.res_model,
            'res_id': self.res_id,
            'view_mode': 'form',
        }

    def _activate(self):
        now = fields.Datetime.now()
        for line in self:
            vals = {'state': 'pending', 'date_pending': now}
            hours = line.step_id.sla_hours if line.step_id else line.company_id.ess_default_sla_hours
            if hours:
                vals['deadline'] = now + timedelta(hours=hours)
            delegates = self.env['bs.hr.approval.delegation']._get_delegates(line.approver_ids)
            if delegates:
                vals['approver_ids'] = [(4, u.id) for u in delegates]
            line.write(vals)

    @api.model
    def _cron_escalate(self):
        now = fields.Datetime.now()
        lines = self.search([('state', '=', 'pending'), ('deadline', '!=', False),
                             ('deadline', '<', now), ('escalated', '=', False)])
        for line in lines:
            request = line._get_request()
            if not request:
                continue
            step = line.step_id
            mode = step.escalation if step else 'users'
            if mode == 'next' and request._ess_has_waiting_line(after=line):
                line.write({'state': 'skipped', 'escalated': True,
                            'comment': request.env._('Escalated automatically (SLA exceeded)')})
                request._ess_activate_next()
            elif mode == 'users':
                extra = step.escalation_user_ids if step else self.env['res.users']
                if not extra:
                    extra = self.env.ref('bs_hr_ess_base.group_ess_hr_manager').sudo().all_user_ids
                line.write({'approver_ids': [(4, u.id) for u in extra], 'escalated': True})
                request._ess_notify_approvers(line, escalation=True)
            else:
                line.write({'escalated': True})
                request._ess_notify_approvers(line, escalation=True)


class BsHrApprovalDelegation(models.Model):
    _name = 'bs.hr.approval.delegation'
    _description = 'ESS Approval Delegation'
    _order = 'date_from desc'

    user_id = fields.Many2one('res.users', required=True, default=lambda self: self.env.user,
                              string='Approver')
    delegate_id = fields.Many2one('res.users', required=True, string='Delegate to')
    date_from = fields.Date(required=True, default=fields.Date.context_today)
    date_to = fields.Date(required=True)
    active = fields.Boolean(default=True)
    note = fields.Char()

    _check_dates = models.Constraint('CHECK(date_to >= date_from)', 'The end date must be after the start date.')

    @api.model
    def _get_delegates(self, users):
        if not users:
            return self.env['res.users']
        today = fields.Date.context_today(self)
        delegations = self.sudo().search(Domain([
            ('user_id', 'in', users.ids), ('date_from', '<=', today), ('date_to', '>=', today)]))
        return delegations.delegate_id - users
