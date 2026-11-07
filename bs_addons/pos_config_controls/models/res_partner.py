from odoo import api, models
from odoo.fields import Domain


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model
    def _load_pos_data_domain(self, data, config):
        domain = super()._load_pos_data_domain(data, config)
        if config.default_customer_id:
            domain = Domain.OR(
                [domain, [("id", "=", config.default_customer_id.id)]]
            )
        return domain
