from odoo import Command, api, fields, models


class PosPreparationDisplayOrderline(models.Model):
    _inherit = "pos_preparation_display.orderline"

    routes_by_parent = fields.Boolean(copy=False)
    modifier_parent_uuid = fields.Char(copy=False, index=True)
    routing_category_ids = fields.Many2many(
        "pos.category",
        "bs_pdis_orderline_routing_category_rel",
        "orderline_id",
        "category_id",
        string="Preparation Routing Categories",
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals_list = []
        for values in vals_list:
            values = dict(values)
            if "routes_by_parent" not in values:
                parent_route = self._get_parent_route_from_values(values)
                if parent_route is not None:
                    parent_uuid, parent_categories = parent_route
                    values.update({
                        "routes_by_parent": True,
                        "modifier_parent_uuid": parent_uuid,
                        "routing_category_ids": [Command.set(parent_categories.ids)],
                    })
            prepared_vals_list.append(values)
        return super().create(prepared_vals_list)

    @api.model
    def _get_parent_route_from_values(self, values):
        preparation_order_id = values.get("preparation_display_order_id")
        source_line_uuid = values.get("pos_order_line_uuid")
        if not preparation_order_id or not source_line_uuid:
            return None

        preparation_order = self.env["pos_preparation_display.order"].browse(
            preparation_order_id
        ).exists()
        pos_order = preparation_order.pos_order_id
        if not pos_order:
            return None

        source_line = pos_order.lines.filtered(
            lambda line: line.uuid == source_line_uuid
        )[:1]
        if not source_line or not source_line.modifier_parent_uuid:
            return None

        parent_line = pos_order.lines.filtered(
            lambda line: line.uuid == source_line.modifier_parent_uuid
        )[:1]
        if not parent_line:
            return None

        return parent_line.uuid, parent_line.product_id.pos_categ_ids

    def _get_modifier_parent_uuid(self):
        """Return the parent UUID, including a fallback for old preparation lines."""
        self.ensure_one()
        if self.modifier_parent_uuid:
            return self.modifier_parent_uuid
        if not self.routes_by_parent or not self.pos_order_line_uuid:
            return False

        pos_order = self.preparation_display_order_id.pos_order_id
        source_line = pos_order.lines.filtered(
            lambda line: line.uuid == self.pos_order_line_uuid
        )[:1]
        return source_line.modifier_parent_uuid if source_line else False

    def _get_routing_category_ids(self):
        self.ensure_one()
        if self.routes_by_parent:
            return self.routing_category_ids
        return self.product_id.pos_categ_ids

    def change_line_status(self, status):
        if not any(self.mapped("routes_by_parent")):
            return super().change_line_status(status)

        orderlines_status = []
        category_ids = set()

        for orderline in self:
            orderline.todo = status[str(orderline.id)]
            category_ids.update(orderline._get_routing_category_ids().ids)
            orderlines_status.append({
                "id": orderline.id,
                "todo": orderline.todo,
            })

        preparation_displays = self.env["pos_preparation_display.display"].search([
            "|",
            ("category_ids", "in", list(category_ids)),
            ("category_ids", "=", False),
        ])
        for preparation_display in preparation_displays:
            preparation_display._notify("CHANGE_ORDERLINE_STATUS", orderlines_status)

        return True
