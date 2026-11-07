from odoo import Command, api, fields, models


class PosPrepLine(models.Model):
    _inherit = "pos.prep.line"

    routes_by_parent = fields.Boolean(copy=False, index=True)
    modifier_parent_uuid = fields.Char(copy=False, index=True)
    routing_category_ids = fields.Many2many(
        "pos.category",
        "bs_pos_prep_line_routing_category_rel",
        "prep_line_id",
        "category_id",
        string="Preparation Routing Categories",
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        prep_lines = super().create(vals_list)
        for prep_line in prep_lines:
            parent_line = prep_line._resolve_modifier_parent_line()
            if not parent_line:
                continue

            prep_line.write({
                "routes_by_parent": True,
                "modifier_parent_uuid": parent_line.uuid,
                "routing_category_ids": [
                    Command.set(parent_line.product_id.pos_categ_ids.ids)
                ],
            })
            prep_line._ensure_modifier_route_states()
        return prep_lines

    def _resolve_modifier_parent_line(self):
        self.ensure_one()
        source_line = self.pos_order_line_id
        if not source_line:
            return self.env["pos.order.line"]

        pos_order = source_line.order_id
        if source_line.modifier_parent_uuid:
            parent_line = pos_order.lines.filtered(
                lambda line: line.uuid == source_line.modifier_parent_uuid
            )[:1]
            if parent_line:
                return parent_line

        parent_reference = source_line.parent_line_id
        if not parent_reference:
            return self.env["pos.order.line"]

        parent_line = pos_order.lines.filtered(
            lambda line: line.uuid == parent_reference
            or str(line.id) == str(parent_reference)
        )[:1]
        return parent_line

    def _routes_to_display(self, prep_display):
        self.ensure_one()
        if not self.routes_by_parent:
            return True
        pos_order = self.prep_order_id.pos_order_id
        allowed_displays = self.env["pos.prep.display"]._get_preparation_displays(
            pos_order,
            self.routing_category_ids.ids,
        )
        return prep_display in allowed_displays

    def _ensure_modifier_route_states(self):
        state_model = self.env["pos.prep.state"]
        for prep_line in self.filtered("routes_by_parent"):
            pos_order = prep_line.prep_order_id.pos_order_id
            displays = self.env["pos.prep.display"]._get_preparation_displays(
                pos_order,
                prep_line.routing_category_ids.ids,
            )
            existing_display_ids = set(
                state_model.search([
                    ("prep_line_id", "=", prep_line.id),
                ]).stage_id.prep_display_id.ids
            )
            values = [
                {
                    "prep_line_id": prep_line.id,
                    "stage_id": display.stage_ids[0].id,
                }
                for display in displays
                if display.stage_ids and display.id not in existing_display_ids
            ]
            if values:
                state_model.create(values)

