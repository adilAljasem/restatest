from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    petty_cash_account_id = fields.Many2one(
        'account.account',
        string="Petty Cash Account",
        domain="[('account_type', '=', 'asset_receivable')]",
        config_parameter='petty_cash.petty_cash_account_id',
    )
