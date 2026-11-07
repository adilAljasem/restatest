from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ess_block_expired_health = fields.Boolean(related='company_id.ess_block_expired_health', readonly=False)
    ess_contract_alert_days = fields.Integer(related='company_id.ess_contract_alert_days', readonly=False)
