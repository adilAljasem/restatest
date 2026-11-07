from odoo import api, models


class AccountAnalyticAccount(models.Model):
    _name = 'account.analytic.account'
    _inherit = ['account.analytic.account', 'pos.load.mixin']

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('company_id', 'in', [config.company_id.id, False])]

    @api.model
    def _load_pos_data_fields(self, config):
        return ['id', 'name']
