from odoo import api, models


class PosPrepState(models.Model):
    _inherit = "pos.prep.state"

    @api.model_create_multi
    def create(self, vals_list):
        allowed_values = []
        pending_routes = set()

        for values in vals_list:
            prep_line = self.env["pos.prep.line"].browse(
                values.get("prep_line_id")
            ).exists()
            if not prep_line or not prep_line.routes_by_parent:
                allowed_values.append(values)
                continue

            stage = self.env["pos.prep.stage"].browse(
                values.get("stage_id")
            ).exists()
            prep_display = stage.prep_display_id
            if not prep_display or not prep_line._routes_to_display(prep_display):
                continue

            route_key = (prep_line.id, prep_display.id)
            duplicate_exists = self.search_count([
                ("prep_line_id", "=", prep_line.id),
                ("stage_id.prep_display_id", "=", prep_display.id),
            ], limit=1)
            if duplicate_exists or route_key in pending_routes:
                continue

            pending_routes.add(route_key)
            allowed_values.append(values)

        return super().create(allowed_values) if allowed_values else self.browse()

