# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class StockRequestTemplate(models.Model):
    _name = "stock.request.template"
    _description = "Stock Request Template"
    _order = "name, id"
    _check_company_auto = True

    name = fields.Char(string="Template Name", required=True, index=True)
    source_warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        string="Source Warehouse",
        check_company=True,
        ondelete="restrict",
        copy=False,
        help="Legacy compatibility field. New templates do not use warehouses.",
    )
    destination_warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        string="Destination Warehouse",
        check_company=True,
        ondelete="restrict",
        copy=False,
        help="Legacy compatibility field. New templates do not use warehouses.",
    )
    line_ids = fields.One2many(
        comodel_name="stock.request.template.line",
        inverse_name="template_id",
        string="Products",
        copy=True,
    )
    priority = fields.Selection(
        selection=[("0", "Normal"), ("1", "Urgent")],
        required=True,
        default="0",
    )
    notes = fields.Text(string="Notes")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )

    _name_company_uniq = models.Constraint(
        "unique(name, company_id)",
        "The Stock Request Template name must be unique per company.",
    )

    @api.constrains(
        "source_warehouse_id",
        "destination_warehouse_id",
        "company_id",
    )
    def _check_warehouses(self):
        for template in self:
            if (
                template.source_warehouse_id
                and template.source_warehouse_id.company_id != template.company_id
            ) or (
                template.destination_warehouse_id
                and template.destination_warehouse_id.company_id
                != template.company_id
            ):
                raise ValidationError(
                    _(
                        "Template warehouses must belong to the template "
                        "company."
                    )
                )


class StockRequestTemplateLine(models.Model):
    _name = "stock.request.template.line"
    _description = "Stock Request Template Line"
    _rec_name = "product_id"
    _order = "sequence, id"
    _check_company_auto = True

    sequence = fields.Integer(default=10)
    template_id = fields.Many2one(
        comodel_name="stock.request.template",
        string="Template",
        required=True,
        ondelete="cascade",
        index=True,
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product",
        required=True,
        check_company=True,
        ondelete="restrict",
        domain=[("type", "in", ["product", "consu"])],
    )
    product_uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="UOM",
        related="product_id.uom_id",
        store=True,
        readonly=True,
    )
    product_uom_qty = fields.Float(
        string="Default Quantity",
        required=True,
        default=1.0,
        digits="Product Unit of Measure",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        related="template_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )

    _template_product_uniq = models.Constraint(
        "unique(template_id, product_id)",
        "A product can only appear once in a Stock Request Template.",
    )
    _positive_quantity = models.Constraint(
        "CHECK(product_uom_qty > 0)",
        "The default quantity must be strictly positive.",
    )

    @api.constrains("product_id", "company_id")
    def _check_product_company(self):
        for line in self:
            if (
                line.product_id.company_id
                and line.product_id.company_id != line.company_id
            ):
                raise ValidationError(
                    _("Template products must belong to the template company.")
                )
