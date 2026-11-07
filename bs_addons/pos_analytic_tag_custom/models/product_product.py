from odoo import api, models


class ProductProduct(models.Model):
    """Override to add write_date to POS data fields"""
    _inherit = 'product.product'

    @api.model
    def _load_pos_data_fields(self, config):
        res = super()._load_pos_data_fields(config)
        # Add write_date which is required by getImageUrl() in the frontend
        # The frontend constructs image URLs like: /web/image?...&unique=${this.write_date}
        if 'write_date' not in res:
            res.append('write_date')
        return res
