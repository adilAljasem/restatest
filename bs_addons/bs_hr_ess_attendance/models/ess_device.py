import hashlib

from odoo import api, fields, models


class BsHrEssDevice(models.Model):
    _name = 'bs.hr.ess.device'
    _description = 'Employee phone (attendance device binding)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='employee_id.company_id', store=True)
    name = fields.Char(string='Device', required=True)
    token_hash = fields.Char(required=True, index=True, groups='base.group_system')
    state = fields.Selection([
        ('active', 'Active'),
        ('pending', 'Waiting HR approval'),
        ('revoked', 'Revoked'),
    ], default='pending', required=True, index=True)
    last_used = fields.Datetime()
    last_ip = fields.Char()

    @api.model
    def _hash(self, token):
        return hashlib.sha256((token or '').encode()).hexdigest()

    def action_approve(self):
        for device in self:
            device.employee_id.sudo().ess_device_ids.filtered(
                lambda d: d.state == 'active' and d != device).write({'state': 'revoked'})
            device.state = 'active'

    def action_revoke(self):
        self.write({'state': 'revoked'})

    @api.model
    def _ess_resolve(self, employee, token, user_agent, ip):
        """Return (device, ok, message)."""
        if not token or len(token) < 16:
            return self.browse(), False, self.env._('This browser could not be identified. Reload the page.')
        token_hash = self._hash(token)
        Device = self.sudo()
        device = Device.search([('employee_id', '=', employee.id), ('token_hash', '=', token_hash)], limit=1)
        if not device:
            has_active = Device.search_count([('employee_id', '=', employee.id), ('state', '=', 'active')])
            device = Device.create({
                'employee_id': employee.id, 'token_hash': token_hash,
                'name': (user_agent or 'Unknown device')[:120],
                'state': 'pending' if has_active else 'active',
            })
            if device.state == 'pending':
                employee._ess_notify_hr_new_device(device)
        device.write({'last_used': fields.Datetime.now(), 'last_ip': ip})
        if device.state == 'active':
            return device, True, ''
        if device.state == 'pending':
            return device, False, self.env._('This phone is not your registered device. HR has been asked to '
                                             'approve it; try again after approval.')
        return device, False, self.env._('This phone has been blocked by HR.')
