from odoo import fields, models
from odoo.exceptions import UserError


class PosConfig(models.Model):
    _inherit = 'pos.config'

    ess_work_location_id = fields.Many2one('hr.work.location', string='Branch (HR)',
                                           help='HR branch of this point of sale (attendance and labor cost).')
    ess_require_attendance = fields.Boolean(
        string='Cashier must be checked in',
        help='The user opening the POS must have an open self-service attendance at this branch.')

    def _ess_check_attendance(self):
        self.ensure_one()
        if not self.ess_require_attendance or not self.ess_work_location_id:
            return
        user = self.env.user
        if user.has_group('point_of_sale.group_pos_manager'):
            return
        employee = user.employee_id
        if not employee:
            raise UserError(self.env._('Your user is not linked to an employee: you cannot open this POS.'))
        open_att = self.env['hr.attendance'].sudo().search_count([
            ('employee_id', '=', employee.id), ('check_out', '=', False),
            ('ess_location_id', '=', self.ess_work_location_id.id)])
        if not open_att:
            raise UserError(self.env._(
                'Check in at %s from the Self-Service app before opening the point of sale.',
                self.ess_work_location_id.name))

    def open_ui(self):
        for config in self:
            config._ess_check_attendance()
        return super().open_ui()
