from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ess_monthly_report_partner_ids = fields.Many2many(
        'res.partner', 'bs_hr_ess_monthly_report_partner_rel', 'company_id', 'partner_id',
        string='Monthly HR report recipients',
        help='Owner / general manager receiving the monthly HR PDF report (HR managers always receive it).')


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ess_monthly_report_partner_ids = fields.Many2many(
        related='company_id.ess_monthly_report_partner_ids', readonly=False)
