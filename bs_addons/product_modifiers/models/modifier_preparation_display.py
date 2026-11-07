from odoo import models


class PosPreparationDisplay(models.Model):
    _inherit = "pos_preparation_display.display"

    def _should_include(self, orderline):
        self.ensure_one()
        if not orderline.routes_by_parent:
            return super()._should_include(orderline)

        display_category_ids = set(self._get_pos_category_ids().ids)
        return bool(
            display_category_ids.intersection(
                orderline._get_routing_category_ids().ids
            )
        )

    def _get_stageless_orders_in_display(self):
        orders = super()._get_stageless_orders_in_display()
        return orders.filtered(
            lambda order: (
                not any(
                    order.preparation_display_order_line_ids.mapped("routes_by_parent")
                )
                or any(
                    self._should_include(orderline)
                    for orderline in order.preparation_display_order_line_ids
                )
            )
        )
