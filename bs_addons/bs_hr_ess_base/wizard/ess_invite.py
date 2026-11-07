import logging

from odoo import fields, models
from odoo.exceptions import UserError

from ..models.otp import normalize_phone

_logger = logging.getLogger(__name__)


class BsHrEssInvite(models.TransientModel):
    _name = 'bs.hr.ess.invite'
    _description = 'Grant ESS portal access'

    employee_ids = fields.Many2many('hr.employee', string='Employees', required=True)
    send_invitation = fields.Boolean(default=True, help='Send the sign-up e-mail to employees having an e-mail.')
    result = fields.Text(readonly=True)

    def action_grant(self):
        self.ensure_one()
        portal_group = self.env.ref('base.group_portal')
        lines = []
        for employee in self.employee_ids:
            if employee.user_id:
                lines.append(self.env._('%s: already has a user (%s)', employee.name, employee.user_id.login))
                continue
            email = employee.work_email or employee.private_email
            phone = normalize_phone(employee.mobile_phone or employee.private_phone)
            login = email or phone
            if not login:
                lines.append(self.env._('%s: skipped - no e-mail and no mobile number', employee.name))
                continue
            Users = self.env['res.users'].sudo().with_context(no_reset_password=True)
            if Users.search_count([('login', '=', login)]):
                lines.append(self.env._('%s: skipped - login %s is already used', employee.name, login))
                continue
            user = Users.create({
                'name': employee.name,
                'login': login,
                'email': email or False,
                'company_id': employee.company_id.id,
                'company_ids': [(6, 0, employee.company_id.ids)],
                'group_ids': [(6, 0, portal_group.ids)],
            })
            employee.sudo().user_id = user
            if email and self.send_invitation:
                try:
                    user.with_context(create_user=True).action_reset_password()
                except UserError as e:
                    _logger.warning('ESS invitation e-mail failed for %s: %s', login, e)
            lines.append(self.env._('%s: portal access created (login %s)', employee.name, login))
        self.result = '\n'.join(lines)
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
            'view_mode': 'form', 'target': 'new',
        }
