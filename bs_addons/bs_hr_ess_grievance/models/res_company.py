from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ess_appeal_days = fields.Integer(
        string='ESS: Appeal period (days)', default=15,
        help='Number of days an employee has to appeal a grievance decision or a disciplinary action.')
