from odoo import api, models


class PosSession(models.Model):
    _inherit = "pos.session"

    @api.model
    def _load_pos_data_models(self, config):
        models_to_load = super()._load_pos_data_models(config)
        insert_at = models_to_load.index("product.product") + 1
        modifier_models = [
            "pos.product.modifier.group.line",
            "pos.modifier.group",
            "pos.modifier.option",
            "pos.product.modifier.option.line",
        ]
        return models_to_load[:insert_at] + modifier_models + models_to_load[insert_at:]
