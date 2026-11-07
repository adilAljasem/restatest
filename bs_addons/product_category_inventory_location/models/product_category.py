from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductCategory(models.Model):
    _inherit = "product.category"

    property_stock_inventory = fields.Many2one(
        "stock.location",
        string="Inventory Location",
        company_dependent=True,
        check_company=True,
        domain="[('usage', '=', 'inventory'), '|', ('company_id', '=', False), ('company_id', '=', allowed_company_ids[0])]",
        help="Inventory location applied to every product in this category for the active company.",
    )

    @api.constrains("property_stock_inventory")
    def _check_inventory_location_company(self):
        for category in self:
            location = category.property_stock_inventory
            if location.company_id and location.company_id != self.env.company:
                raise ValidationError(_(
                    "The Inventory Location must either be shared or belong to the active company."
                ))

    def write(self, vals):
        result = super().write(vals)
        if "property_stock_inventory" in vals:
            company = self.env.company
            products = self.env["product.template"].with_context(active_test=False).search([
                ("categ_id", "in", self.ids),
                ("company_id", "in", [False, company.id]),
            ])
            products._sync_inventory_location_from_category()
        return result
