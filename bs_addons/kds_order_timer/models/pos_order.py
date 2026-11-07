from odoo import api, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    @api.model
    def _load_pos_preparation_data_fields(self):
        fields = super()._load_pos_preparation_data_fields()
        # قائمة فارغة تعني أن كل الحقول تُرسل أصلاً
        if fields and "tracking_number" not in fields:
            fields = list(fields) + ["tracking_number"]
        return fields
