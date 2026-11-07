from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class StockInventoryTemplate(models.Model):
    _name = 'stock.inventory.template'
    _description = 'Inventory Template'
    _order = 'name, id'
    _check_company_auto = True

    name = fields.Char(required=True)
    code = fields.Char(index=True)
    active = fields.Boolean(default=True)
    is_active = fields.Boolean(string='Active', related='active', readonly=False)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company,
    )
    line_ids = fields.One2many(
        'stock.inventory.template.line', 'template_id', string='Products', copy=True,
    )


class StockInventoryTemplateLine(models.Model):
    _name = 'stock.inventory.template.line'
    _description = 'Inventory Template Product'
    _rec_name = 'product_id'
    _order = 'id'
    _check_company_auto = True

    template_id = fields.Many2one(
        'stock.inventory.template', required=True, ondelete='cascade', index=True,
        check_company=True,
    )
    company_id = fields.Many2one(related='template_id.company_id', store=True)
    product_id = fields.Many2one(
        'product.product', required=True, ondelete='restrict', check_company=True,
        domain=[('is_storable', '=', True), ('type', '=', 'consu')],
    )

    _unique_product = models.Constraint(
        'UNIQUE(template_id, product_id)',
        'A product can only appear once in the same inventory template.',
    )

    @api.constrains('product_id')
    def _check_storable_product(self):
        for line in self:
            if not line.product_id.is_storable or line.product_id.type != 'consu':
                raise ValidationError(_('Inventory templates only support storable products.'))
