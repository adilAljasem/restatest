from odoo import api, fields, models


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    selected_component_qty = fields.Float(
        string="Component Qty",
        compute="_compute_selected_component",
        digits="Product Unit",
    )
    selected_component_uom_id = fields.Many2one(
        "uom.uom", string="Component UoM", compute="_compute_selected_component"
    )

    @api.depends(
        "bom_line_ids.product_id",
        "bom_line_ids.product_id.product_tmpl_id",
        "bom_line_ids.product_qty",
        "bom_line_ids.product_uom_id",
    )
    @api.depends_context(
        "selected_component_product_id", "selected_component_product_tmpl_id"
    )
    def _compute_selected_component(self):
        product_id = self.env.context.get("selected_component_product_id")
        product_tmpl_id = self.env.context.get("selected_component_product_tmpl_id")
        for bom in self:
            if product_id:
                lines = bom.bom_line_ids.filtered(
                    lambda line: line.product_id.id == product_id
                )
            elif product_tmpl_id:
                lines = bom.bom_line_ids.filtered(
                    lambda line: line.product_tmpl_id.id == product_tmpl_id
                )
            else:
                lines = self.env["mrp.bom.line"]
            if not lines:
                bom.selected_component_qty = 0.0
                bom.selected_component_uom_id = False
                continue

            unit = lines[0].product_uom_id
            bom.selected_component_qty = sum(
                line.product_uom_id._compute_quantity(line.product_qty, unit)
                for line in lines
            )
            bom.selected_component_uom_id = unit
