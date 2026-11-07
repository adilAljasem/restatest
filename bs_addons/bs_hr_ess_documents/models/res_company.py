from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ess_block_expired_health = fields.Boolean(
        string='ESS: Block check-in on expired critical document',
        help='Refuse the self-service check-in when the employee has an expired document whose type '
             'blocks attendance (e.g. health certificate). When disabled, the supervisor is only notified.')
    ess_contract_alert_days = fields.Integer(
        string='ESS: Contract renewal notice (days)', default=60,
        help='A contract renewal workflow is opened this many days before the contract end date.')
