from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ess_show_birthdays = fields.Boolean(
        string='ESS: Show birthdays and work anniversaries',
        help='Show the birthdays and work anniversaries of the colleagues of the same branch '
             'in the portal calendar (name and day only).')
