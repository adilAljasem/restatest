from odoo import api, models


class RestaurantFloor(models.Model):
    _inherit = "restaurant.floor"

    @api.model_create_multi
    def create(self, vals_list):
        floors = super().create(vals_list)
        floors.pos_config_ids._sfs_sync_trusted()
        return floors

    def write(self, vals):
        res = super().write(vals)
        if "pos_config_ids" in vals:
            self.pos_config_ids._sfs_sync_trusted()
        return res
