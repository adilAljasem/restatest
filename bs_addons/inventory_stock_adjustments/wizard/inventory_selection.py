# -*- coding: utf-8 -*-
from odoo import fields, models, api, _
from odoo.exceptions import UserError

class InventorySelection(models.TransientModel):
    _name = "inventory.selection"
    _description = "Stock Inventory Line Selection"

    product_id = fields.Many2one('product.product', string="Product")
    add_product_qty = fields.Integer(string="Add Product Quantity")
    selection_line_id = fields.One2many('inventory.selection.line','inventory_selection_id')
    stock_inventory_id = fields.Many2one('stock.inventory')

    def set_product_qty(self):
        if self.selection_line_id:
            self.selection_line_id.write({'quantity_on_hand':1})
        return {
            'name': "Add Products",
            'view_mode': 'form',
            'view_type': 'form',
            'view_id': self.env.ref("inventory_stock_adjustments.inventory_selection_form_view").id,
            'res_model': 'inventory.selection',
            'res_id':self.id,
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

    def add_product_lines(self):
        selection_line_id = []
        data_dict = {
            'inventory_selection_id':self.id,
            'quantity': 1.00 if self.product_id.tracking == "serial" else 0.00
            }
        for _ in range(self.add_product_qty):
            selection_line_id.append((0,0,data_dict))
        self.write({'selection_line_id':selection_line_id})

        return {
            'name': "Add Products",
            'view_mode': 'form',
            'view_type': 'form',
            'view_id': self.env.ref("inventory_stock_adjustments.inventory_selection_form_view").id,
            'res_model': 'inventory.selection',
            'res_id':self.id,
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

    def confirm_add_products(self):
        if self.stock_inventory_id.state != 'confirm':
            raise UserError(_("You can only confirm stock inventory in confirm state"))
        create_stock_inventory_lines = []
        for line in self.selection_line_id:
            if line.stock_inventory_line_id and line.lot_id:
                line.stock_inventory_line_id.write({'inventory_quantity':line.quantity})
            elif line.stock_inventory_line_id and line.lot_serial:
                pass
            else:
                create_stock_inventory_lines.append((0,0,{
                    'product_id':self.product_id.id,
                    'prod_lot_id':line.prod_lot_id.id,
                    'inventory_quantity':line.quantity,
                }))
            self.stock_inventory_id.write({'line_ids':create_stock_inventory_lines})
            if create_stock_inventory_lines:
                self.stock_inventory_id.line_ids.filtered(lambda x: x.product_id == self.product_id and not x.prod_lot_id and x.lot_serial).unlink()


class InventorySelectionLine(models.TransientModel):
    _name = "inventory.selection.line"
    _description = "Stock Inventory Selection Line"

    def get_default_qty(self):
        if self.inventory_selection_id:
            return 0.0
        return 1
    inventory_selection_id = fields.Many2one('inventory.selection')
    product_uom_id = fields.Many2one(
        'uom.uom', 'Product Unit of Measure',
        readonly=True,
        related='inventory_selection_id.product_id.uom_id',
        )
    lot_id = fields.Boolean(string='Lot',readonly=True, compute="compute_lot_serial_required")
    lot_serial = fields.Boolean(string='Serial',readonly=True, compute="compute_lot_serial_required")
    quantity_on_hand = fields.Float(
        'Quantity On Hand',
        readonly=True,
        digits='Product Unit of Measure')
    quantity = fields.Float(string="Quantity", default=get_default_qty)
    prod_lot_id = fields.Many2one(
        'stock.lot', 'Lot Number')
    stock_inventory_line_id = fields.Many2one('stock.inventory.line')
    product_id = fields.Many2one('product.product', related="inventory_selection_id.product_id", string="Product")

    @api.depends('inventory_selection_id.product_id')
    def compute_lot_serial_required(self):
        for line in self:
            line.lot_serial = False
            if line.inventory_selection_id.product_id.tracking == 'serial':
                line.lot_serial = True
            line.lot_id = False
            if line.inventory_selection_id.product_id.tracking == 'lot':
                line.lot_id = True
