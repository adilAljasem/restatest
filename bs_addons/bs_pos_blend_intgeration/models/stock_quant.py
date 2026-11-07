from odoo import api, models

from ..services.stock_availability import BlendStockAvailabilityService


class StockQuant(models.Model):
    _inherit = "stock.quant"

    def _blend_stock_pairs(self):
        return {(quant.product_id.id, quant.location_id.id) for quant in self.sudo()}

    @api.model_create_multi
    def create(self, vals_list):
        service = BlendStockAvailabilityService(self.env)
        before = service.capture({
            (vals.get("product_id") or self.env.context.get("default_product_id"),
             vals.get("location_id") or self.env.context.get("default_location_id"))
            for vals in vals_list
        })
        records = super().create(vals_list)
        service.enqueue(before)
        return records

    def write(self, vals):
        if not {"quantity", "reserved_quantity", "product_id", "location_id"}.intersection(vals):
            return super().write(vals)
        service = BlendStockAvailabilityService(self.env)
        pairs = self._blend_stock_pairs()
        pairs |= {
            (vals.get("product_id", product_id), vals.get("location_id", location_id))
            for product_id, location_id in pairs
        }
        before = service.capture(pairs)
        result = super().write(vals)
        service.enqueue(before)
        return result

    def unlink(self):
        service = BlendStockAvailabilityService(self.env)
        before = service.capture(self.filtered(lambda quant: quant.quantity or quant.reserved_quantity)._blend_stock_pairs())
        result = super().unlink()
        service.enqueue(before)
        return result
