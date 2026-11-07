from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError


class BsHrGoal(models.Model):
    _name = 'bs.hr.goal'
    _description = 'Employee Goal'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'state, target_date, id desc'

    name = fields.Char(required=True, tracking=True)
    description = fields.Text()
    employee_id = fields.Many2one('hr.employee', required=True, index=True, tracking=True)
    manager_id = fields.Many2one('hr.employee', string='Manager', index=True,
                                 compute='_compute_manager_id', store=True, readonly=False)
    company_id = fields.Many2one(related='employee_id.company_id', store=True)
    work_location_id = fields.Many2one(related='employee_id.work_location_id', store=True, string='Branch')
    target_date = fields.Date(tracking=True)
    progress = fields.Integer(default=0, tracking=True, aggregator='avg')
    state = fields.Selection([
        ('open', 'In progress'),
        ('achieved', 'Achieved'),
        ('missed', 'Missed'),
    ], default='open', required=True, tracking=True)
    last_update_note = fields.Text(string='Last update')

    _progress_range = models.Constraint('CHECK(progress >= 0 AND progress <= 100)',
                                        'The progress must be between 0 and 100.')

    @api.depends('employee_id')
    def _compute_manager_id(self):
        for goal in self:
            if not goal.manager_id:
                goal.manager_id = goal.employee_id.sudo().parent_id

    def write(self, vals):
        if not self.env.su and not self.env.user.has_group('bs_hr_ess_base.group_ess_hr_officer'):
            user = self.env.user
            keys = set(vals) - {'message_main_attachment_id'}
            for goal in self:
                if goal.manager_id.user_id == user and goal.employee_id.user_id != user:
                    continue
                if goal.employee_id.user_id == user and keys <= {'progress', 'last_update_note'} \
                        and goal.state == 'open':
                    continue
                raise AccessError(self.env._('You cannot modify the goal %s.', goal.name))
        return super().write(vals)

    def _ess_update_progress(self, progress, note=None):
        """Called by the employee (portal, sudo) to report progress."""
        self.ensure_one()
        if self.state != 'open':
            raise ValidationError(self.env._('This goal is closed.'))
        progress = max(0, min(100, int(progress)))
        self.write({'progress': progress, 'last_update_note': note or False})
        self.message_post(body=self.env._('Progress updated to %(p)s%%. %(note)s', p=progress, note=note or ''))
        if progress == 100 and self.manager_id.user_id and not self.manager_id.user_id.share:
            self.activity_schedule('mail.mail_activity_data_todo', user_id=self.manager_id.user_id.id,
                                   summary=self.env._('Goal reached 100%%: %s', self.name))

    def action_achieved(self):
        self.write({'state': 'achieved', 'progress': 100})

    def action_missed(self):
        self.write({'state': 'missed'})

    def action_reopen(self):
        self.write({'state': 'open'})
