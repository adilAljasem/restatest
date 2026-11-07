from odoo import api, fields, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    delivery_order_ids = fields.One2many(
        "bs.delivery.order", "pos_order_id", readonly=True,
    )
    tryorder_delivery_order_id = fields.Many2one(
        "bs.delivery.order",
        string="TryOrder Delivery Order",
        compute="_compute_tryorder_delivery_order_id",
        store=True,
        index=True,
        readonly=True,
    )
    tryorder_external_order_id = fields.Char(
        string="TryOrder Order ID",
        related="tryorder_delivery_order_id.external_order_id",
        store=True,
        index=True,
        readonly=True,
    )
    tryorder_status = fields.Selection(
        string="TryOrder Status",
        related="tryorder_delivery_order_id.state",
        store=True,
        index=True,
        readonly=True,
    )

    @api.depends("delivery_order_ids", "delivery_order_ids.provider_id.code")
    def _compute_tryorder_delivery_order_id(self):
        for pos_order in self:
            pos_order.tryorder_delivery_order_id = pos_order.delivery_order_ids.filtered(
                lambda order: order.provider_id.code == "tryorder"
            ).sorted("id")[:1]
