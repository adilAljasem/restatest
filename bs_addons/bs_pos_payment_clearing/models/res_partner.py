from odoo import api, models
from odoo.fields import Domain


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model
    def _load_pos_data_domain(self, data, config):
        domain = super()._load_pos_data_domain(data, config)
        partner_ids = config.payment_method_ids.filtered("split_transactions").partner_id.ids
        return Domain.OR([domain, [("id", "in", partner_ids)]]) if partner_ids else domain
