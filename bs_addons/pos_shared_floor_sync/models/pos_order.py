from odoo import api, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    @api.model
    def _load_pos_data_domain(self, data, config):
        """عند فتح الجلسة: حمّل أيضاً الطلبات المفتوحة على طاولات الصناديق المشتركة،
        حتى تظهر الطاولات المشغولة فوراً. التحديثات اللاحقة يتكفل بها Odoo نفسه عبر الـ bus."""
        domain = super()._load_pos_data_domain(data, config)
        if config.module_pos_restaurant and config.trusted_config_ids:
            domain = [
                "|",
                *domain,
                "&", "&",
                ("state", "=", "draft"),
                ("config_id", "in", config.trusted_config_ids.ids),
                ("table_id", "in", config.floor_ids.table_ids.ids),
            ]
        return domain
