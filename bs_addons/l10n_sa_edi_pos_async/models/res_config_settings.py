from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_l10n_sa_zatca_async = fields.Boolean(related='pos_config_id.l10n_sa_zatca_async', readonly=False)
