
from odoo import api, fields, models


class PosSession(models.Model):
    """To add analytic tags in pos session"""
    _inherit = 'pos.session'

    pos_analytic_account_id = fields.Many2one('account.analytic.account',
                                              string='Pos Analytic Account',
                                              help="Pos Analytic account in pos session",
                                              readonly=True)

    @api.model
    def _load_pos_data_models(self, config):
        res = super()._load_pos_data_models(config)
        res += ['account.analytic.account']
        return res

    def _create_account_move(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        res = super()._create_account_move(balancing_account, amount_to_balance, bank_payment_method_diffs)
        self.pos_analytic_account_id = self.config_id.analytic_account_id
        if self.pos_analytic_account_id:
            # In Odoo 19, analytic_distribution is a Json field.
            # We use string keys for the analytic account IDs.
            distribution = {str(self.pos_analytic_account_id.id): 100.0}
            self.move_id.line_ids.write({'analytic_distribution': distribution})
        return res