from odoo import api, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    def _sfs_shared_configs(self):
        """نقاط البيع الأخرى (مطعم) التي تشترك مع هذه النقطة في صالة واحدة على الأقل."""
        self.ensure_one()
        return (self.floor_ids.pos_config_ids - self).filtered(
            lambda c: c.module_pos_restaurant and c.company_id == self.company_id
        )

    def _sfs_sync_trusted(self):
        """Odoo 19 يزامن الطلبات المفتوحة بين نقاط البيع "الموثوقة" (trusted_config_ids)،
        لكن شاشة الإعدادات تمنع اختيار نقاط بيع المطعم. هنا نربطها تلقائياً وبشكل متبادل."""
        for config in self.filtered("module_pos_restaurant"):
            shared = config._sfs_shared_configs()
            missing = shared - config.trusted_config_ids
            if missing:
                config.sudo().with_context(sfs_no_sync=True).trusted_config_ids = [
                    (4, c.id) for c in missing
                ]

    @api.model_create_multi
    def create(self, vals_list):
        configs = super().create(vals_list)
        configs._sfs_sync_trusted()
        (configs.floor_ids.pos_config_ids - configs)._sfs_sync_trusted()
        return configs

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get("sfs_no_sync"):
            return res
        if "floor_ids" in vals or "module_pos_restaurant" in vals:
            related = self | self.floor_ids.pos_config_ids
            related._sfs_sync_trusted()
        return res
