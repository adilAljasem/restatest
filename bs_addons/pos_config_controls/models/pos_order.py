from odoo import api, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            session = self.env["pos.session"].browse(vals.get("session_id"))
            if session.config_id.always_to_invoice:
                vals["to_invoice"] = True
        return super().create(vals_list)

    def write(self, vals):
        if "to_invoice" in vals and not vals["to_invoice"]:
            forced_orders = self.filtered("config_id.always_to_invoice")
            if forced_orders:
                regular_orders = self - forced_orders
                forced_vals = {**vals, "to_invoice": True}
                result = super(PosOrder, forced_orders).write(forced_vals)
                if regular_orders:
                    result = super(PosOrder, regular_orders).write(vals) and result
                return result
        return super().write(vals)
