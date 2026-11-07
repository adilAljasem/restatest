from odoo import models


class PosPreparationDisplayOrder(models.Model):
    _inherit = "pos_preparation_display.order"

    def change_order_stage(self, stage_id, preparation_display_id):
        self.ensure_one()
        if not any(
            self.preparation_display_order_line_ids.mapped("routes_by_parent")
        ):
            return super().change_order_stage(stage_id, preparation_display_id)

        preparation_display = self.env["pos_preparation_display.display"].browse(
            preparation_display_id
        )

        for orderline in self.preparation_display_order_line_ids:
            orderline.todo = True

        belongs_to_display = any(
            preparation_display._should_include(orderline)
            for orderline in self.preparation_display_order_line_ids
        )
        if belongs_to_display and stage_id in preparation_display.stage_ids.ids:
            current_stage = self.order_stage_ids.create({
                "preparation_display_id": preparation_display.id,
                "stage_id": stage_id,
                "order_id": self.id,
                "done": False,
            })
            preparation_display._notify("CHANGE_ORDER_STAGE", {
                "order_id": self.id,
                "last_stage_change": current_stage.write_date,
                "stage_id": stage_id,
            })
            return current_stage.write_date

        return False

    def _export_for_ui(self, preparation_display):
        result = super()._export_for_ui(preparation_display)
        if not result:
            return result

        orderline_by_id = {
            orderline.id: orderline
            for orderline in self.preparation_display_order_line_ids
        }
        for line_values in result["orderlines"]:
            orderline = orderline_by_id.get(line_values["id"])
            if orderline:
                parent_uuid = orderline._get_modifier_parent_uuid()
                line_values["product_category_ids"] = (
                    orderline._get_routing_category_ids().ids
                )
                line_values.update({
                    "pos_order_line_uuid": orderline.pos_order_line_uuid,
                    "modifier_parent_uuid": parent_uuid or False,
                    "is_modifier_child": bool(parent_uuid),
                })
        return result
