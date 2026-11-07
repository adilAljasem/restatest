from odoo import models


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _process_preparation_changes(self, options):
        result = super()._process_preparation_changes(options)
        if not result.get("change"):
            return result

        routed_lines = self.env["pos.prep.line"].search([
            ("prep_order_id.pos_order_id", "=", self.id),
            ("routes_by_parent", "=", True),
        ])
        if routed_lines:
            result["category_ids"] = set(result.get("category_ids", []))
            result["category_ids"].update(routed_lines.routing_category_ids.ids)
        return result
