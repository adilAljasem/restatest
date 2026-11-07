from collections import defaultdict

from odoo import api, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def _sync_inventory_location_from_category(self):
        """Apply the category location in each product's effective company."""
        products_by_company = defaultdict(list)
        products_by_value = defaultdict(list)

        for product in self:
            company = product.company_id or self.env.company
            products_by_company[company.id].append(product.id)

        for company_id, product_ids in products_by_company.items():
            for product in self.browse(product_ids).with_company(company_id):
                location = product.categ_id.property_stock_inventory

                if product.property_stock_inventory != location:
                    products_by_value[company_id, location.id].append(product.id)

        for (company_id, location_id), product_ids in products_by_value.items():
            self.browse(product_ids).with_company(company_id).write({
                "property_stock_inventory": location_id,
            })

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        products._sync_inventory_location_from_category()
        return products

    def write(self, vals):
        result = super().write(vals)
        if "categ_id" in vals or "company_id" in vals:
            self._sync_inventory_location_from_category()
        return result
