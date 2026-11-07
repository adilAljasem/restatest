from odoo import fields, models


class HrWorkLocation(models.Model):
    _inherit = 'hr.work.location'

    ess_manager_id = fields.Many2one(
        'res.users', string='Branch Manager',
        help='Used by approval flows with the "Branch manager" approver type.')
    ess_employee_count = fields.Integer(compute='_compute_ess_employee_count', string='Employees')

    def _compute_ess_employee_count(self):
        data = self.env['hr.employee']._read_group(
            [('work_location_id', 'in', self.ids)], ['work_location_id'], ['__count'])
        counts = {loc.id: count for loc, count in data}
        for loc in self:
            loc.ess_employee_count = counts.get(loc.id, 0)
