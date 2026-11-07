from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ess_leave_max_concurrent = fields.Integer(
        string='ESS: Max colleagues on leave',
        help='Warn approvers when this number of colleagues of the same branch and job are already '
             'on leave during the requested period. 0 disables the check.')
    ess_custody_location_id = fields.Many2one(
        'stock.location', string='ESS: Employee custody location', copy=False,
        help='Internal location holding the items handed over to employees. Created automatically.')

    def _ess_custody_location(self):
        """Return (and create on demand) the 'Employee Custody' location of the company."""
        self.ensure_one()
        location = self.sudo().ess_custody_location_id
        if location:
            return location
        warehouse = self.env['stock.warehouse'].sudo().search([('company_id', '=', self.id)], limit=1)
        location = self.env['stock.location'].sudo().create({
            'name': self.env._('Employee Custody'),
            'usage': 'internal',
            'location_id': warehouse.view_location_id.id if warehouse else False,
            'company_id': self.id,
        })
        self.sudo().ess_custody_location_id = location
        return location
