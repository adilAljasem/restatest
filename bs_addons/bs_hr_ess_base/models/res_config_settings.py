from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ess_otp_login = fields.Boolean(related='company_id.ess_otp_login', readonly=False)
    ess_brand_color = fields.Char(related='company_id.ess_brand_color', readonly=False)
    ess_app_name = fields.Char(related='company_id.ess_app_name', readonly=False)
    ess_portal_landing = fields.Boolean(related='company_id.ess_portal_landing', readonly=False)
    ess_default_sla_hours = fields.Integer(related='company_id.ess_default_sla_hours', readonly=False)
