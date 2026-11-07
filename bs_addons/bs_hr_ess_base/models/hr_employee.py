from odoo import api, fields, models
from odoo.fields import Domain


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    ess_access = fields.Selection([
        ('none', 'No access'),
        ('portal', 'Portal user'),
        ('internal', 'Internal user'),
    ], compute='_compute_ess_access', string='Self-Service Access')
    ess_notification_ids = fields.One2many('bs.hr.notification', 'employee_id', groups='hr.group_hr_user')
    ess_adjustment_ids = fields.One2many('bs.hr.payroll.adjustment', 'employee_id',
                                         groups='hr.group_hr_user', string='Payroll Adjustments')

    @api.depends('user_id', 'user_id.share')
    def _compute_ess_access(self):
        for employee in self:
            if not employee.user_id:
                employee.ess_access = 'none'
            elif employee.user_id.share:
                employee.ess_access = 'portal'
            else:
                employee.ess_access = 'internal'

    # ------------------------------------------------------------------
    # portal helpers (always called in sudo from controllers after the
    # employee has been resolved from the logged-in user)
    # ------------------------------------------------------------------
    @api.model
    def _ess_get_for_user(self, user=None):
        user = user or self.env.user
        Employee = self.sudo()
        domain = Domain('user_id', '=', user.id)
        employee = Employee.search(domain & Domain('company_id', '=', user.company_id.id), limit=1)
        return employee or Employee.search(domain, limit=1)

    def _ess_team(self):
        """Employees this employee manages (direct reports, department, branch)."""
        self.ensure_one()
        Employee = self.sudo()
        domain = Domain('parent_id', 'child_of', self.id) & Domain('id', '!=', self.id)
        if self.user_id:
            departments = self.env['hr.department'].sudo().search([('manager_id', '=', self.id)])
            branches = self.env['hr.work.location'].sudo().search([('ess_manager_id', '=', self.user_id.id)])
            if departments:
                domain |= Domain('department_id', 'child_of', departments.ids)
            if branches:
                domain |= Domain('work_location_id', 'in', branches.ids)
        return Employee.search(domain & Domain('id', '!=', self.id))

    def _ess_is_manager(self):
        self.ensure_one()
        return bool(self._ess_team())

    def _ess_attendance_precheck(self, location=None):
        """Return a list of blocking messages preventing a check-in.

        Extended by other modules (e.g. expired health certificate).
        """
        return []

    def _ess_dashboard_cards(self):
        """Cards displayed on the portal dashboard.

        Each module appends dicts: {'key', 'title', 'value', 'subtitle', 'url',
        'icon' (font-awesome class), 'tone' (success/warning/danger/info), 'sequence'}.
        """
        self.ensure_one()
        cards = []
        pending = 0
        for model_name in self.env['bs.hr.approval.flow']._selection_res_model():
            pending += self.env[model_name[0]].sudo().search_count([
                ('employee_id', '=', self.id), ('state', 'in', ('submitted', 'in_approval'))])
        cards.append({
            'key': 'requests', 'title': self.env._('Pending requests'), 'value': pending,
            'subtitle': self.env._('Waiting for approval'), 'url': '/my/hr/requests',
            'icon': 'fa-hourglass-half', 'tone': 'warning' if pending else 'info', 'sequence': 50,
        })
        unread = self.env['bs.hr.notification'].sudo().search_count([
            ('employee_id', '=', self.id), ('is_read', '=', False)])
        cards.append({
            'key': 'notifications', 'title': self.env._('Notifications'), 'value': unread,
            'subtitle': self.env._('Unread'), 'url': '/my/hr/notifications',
            'icon': 'fa-bell', 'tone': 'danger' if unread else 'info', 'sequence': 90,
        })
        return cards

    def _ess_menu_items(self):
        """Portal navigation entries: {'key','title','url','icon','sequence','new_request'}.

        ``new_request`` entries are also listed on the "New request" page.
        """
        self.ensure_one()
        items = [
            {'key': 'home', 'title': self.env._('Home'), 'url': '/my/hr', 'icon': 'fa-home', 'sequence': 1},
            {'key': 'requests', 'title': self.env._('My requests'), 'url': '/my/hr/requests',
             'icon': 'fa-list-ul', 'sequence': 40},
            {'key': 'profile', 'title': self.env._('My profile'), 'url': '/my/hr/profile',
             'icon': 'fa-user', 'sequence': 80},
            {'key': 'profile_change', 'title': self.env._('Update my details'),
             'url': '/my/hr/profile/change', 'icon': 'fa-pencil', 'sequence': 85, 'new_request': True},
            {'key': 'eos', 'title': self.env._('End of service calculator'), 'url': '/my/hr/eos',
             'icon': 'fa-calculator', 'sequence': 95},
        ]
        if self._ess_is_manager():
            items.append({'key': 'team', 'title': self.env._('My team'), 'url': '/my/hr/team',
                          'icon': 'fa-users', 'sequence': 30})
        return items

    def _ess_service_start(self):
        self.ensure_one()
        versions = self.sudo().with_context(active_test=False).version_ids
        starts = [d for d in versions.mapped('contract_date_start') if d]
        return min(starts) if starts else (self.sudo().create_date.date() if self.create_date else None)

    def _ess_monthly_wage(self):
        self.ensure_one()
        return self.sudo().version_id.wage or 0.0

    # ------------------------------------------------------------------
    # backend actions
    # ------------------------------------------------------------------
    def action_ess_invite(self):
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Grant self-service access'),
            'res_model': 'bs.hr.ess.invite',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_employee_ids': [(6, 0, self.ids)]},
        }
