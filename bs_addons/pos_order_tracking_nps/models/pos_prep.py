from odoo import api, models


class PosPrepState(models.Model):
    _inherit = 'pos.prep.state'

    def change_state_stage(self, stages, prep_display_id):
        res = super().change_state_stage(stages, prep_display_id)
        self.prep_line_id.prep_order_id.pos_order_id._tracking_sync()
        return res


class PosPrepOrder(models.Model):
    _inherit = 'pos.prep.order'

    @api.model
    def process_order(self, order_id, options={}):
        res = super().process_order(order_id, options)
        if order_id:
            order = self.env['pos.order'].browse(order_id).exists()
            order._tracking_sync()
        return res
