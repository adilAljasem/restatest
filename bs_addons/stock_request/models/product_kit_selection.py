from odoo import api, fields, models


class ProductProduct(models.Model):
    _inherit = "product.product"

    stock_request_is_kit = fields.Boolean(
        compute="_compute_stock_request_is_kit",
        search="_search_stock_request_is_kit",
    )

    def _search(self, domain, offset=0, limit=None, order=None, *,
                active_test=True, bypass_access=False):
        # Apply to both autocomplete and Search More, only for this module's
        # product selectors. Ordinary product searches/read remain unchanged.
        if self.env.context.get("stock_request_hide_kits"):
            domain = fields.Domain(domain) & ~self._stock_request_kit_domain()
        return super()._search(domain, offset=offset, limit=limit, order=order,
                               active_test=active_test, bypass_access=bypass_access)

    @api.model
    def _stock_request_kit_domain(self):
        # Manufacturing is optional. Read only BOM classification with elevated
        # access, so warehouse users do not need manufacturing permissions.
        if "mrp.bom" not in self.env:
            return fields.Domain.FALSE
        company_id = self.env.context.get("stock_request_company_id") or self.env.company.id
        if company_id not in self.env.companies.ids:
            company_id = self.env.company.id
        boms = self.env["mrp.bom"].sudo().search([
            ("type", "=", "phantom"), ("active", "=", True),
            ("company_id", "in", [False, company_id]),
        ])
        return fields.Domain.OR([
            fields.Domain("id", "in", boms.mapped("product_id").ids),
            fields.Domain("product_tmpl_id", "in", boms.filtered(
                lambda bom: not bom.product_id
            ).mapped("product_tmpl_id").ids),
        ])

    @api.depends_context("company", "stock_request_company_id")
    def _compute_stock_request_is_kit(self):
        kits = self.with_context(stock_request_hide_kits=False).filtered_domain(self._stock_request_kit_domain())
        for product in self:
            product.stock_request_is_kit = product in kits

    def _search_stock_request_is_kit(self, operator, value):
        if operator not in ("=", "!=", "in", "not in"):
            return NotImplemented
        values = value if operator in ("in", "not in") else [value]
        wants_true = True in values
        wants_false = False in values
        domain = self._stock_request_kit_domain()
        if wants_true and wants_false:
            domain = fields.Domain.TRUE
        elif wants_false:
            domain = ~domain
        elif not wants_true:
            domain = fields.Domain.FALSE
        return ~domain if operator in ("!=", "not in") else domain
