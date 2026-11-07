# -*- coding: utf-8 -*-

from odoo import fields, models, api, _
from odoo.exceptions import UserError

_FREEZE_ONHAND_TOKEN = object()


class StockInventoryLine(models.Model):
    _name = 'stock.inventory.line'
    _description = 'Inventory line'

    stock_inventory_id = fields.Many2one('stock.inventory', index=True)
    force_counting_date = fields.Boolean(
        related='stock_inventory_id.force_counting_date',
        readonly=True,
    )
    product_id = fields.Many2one(
        'product.product', 'Product',
        index=True, required=True)
    product_uom_id = fields.Many2one(
        'uom.uom', 'Product Unit of Measure',
        required=True,
        readonly=True,
        related='product_id.uom_id',
    )
    location_id = fields.Many2one(
        'stock.location', 'Location',
        index=True, required=True, related='stock_inventory_id.location_id')
    prod_lot_id = fields.Many2one(
        'stock.lot', 'Lot Number',
        domain="[('product_id', '=', product_id)]",
    )
    package_id = fields.Many2one(
        'stock.package', 'Pack', index=True)
    partner_id = fields.Many2one('res.partner', 'Owner')

    # --- On Hand: dynamically computed from the selected Inventoried Location only ---
    location_quantity = fields.Float(
        'On Hand',
        help='Current quantity at the inventoried location only. '
             'Automatically reflects the latest stock movements.',
        compute='_compute_location_quantities',
        digits='Product Unit of Measure',
    )
    # Frozen snapshot alias used at validation time
    quantity = fields.Float(
        'Quantity On Hand',
        digits='Product Unit of Measure',
        help='Quantity at the inventoried location (frozen upon validation).',
        compute='_compute_quantity',
    )
    validated_quantity = fields.Float(
        readonly=True, copy=False, digits='Product Unit of Measure',
    )
    quantity_frozen = fields.Boolean(readonly=True, copy=False)

    # --- Reserved & Available: dynamically computed per-location only ---
    reserved_quantity = fields.Float(
        'Reserved',
        readonly=True,
        digits='Product Unit of Measure',
        compute='_compute_location_quantities',
    )
    available_quantity = fields.Float(
        'Available',
        help="On hand quantity which hasn't been reserved on a transfer, "
             "at the inventoried location only.",
        digits='Product Unit of Measure',
        compute='_compute_location_quantities',
    )

    # --- Counted Quantity: static, physically counted, never auto-changes on movements ---
    inventory_quantity = fields.Float(
        'Counted Quantity', digits='Product Unit of Measure',
        help="The product's physically counted quantity. Represents the physically counted "
             "quantity and does not change due to subsequent stock movements.")

    # --- Different = Counted Quantity - On Hand ---
    different = fields.Float(
        string='Different',
        compute='_compute_different',
        digits='Product Unit of Measure',
    )

    # --- Diff. Valuation = Different * product cost ---
    currency_id = fields.Many2one(
        'res.currency', 'Currency',
        related='company_id.currency_id', readonly=True, required=True)
    price_value = fields.Monetary(
        'Diff. Valuation',
        compute='_compute_price_value',
        currency_field='currency_id',
    )

    # --- Backdating fields ---
    qty_after_counting_date = fields.Float(
        'Stock Movements After Counting Date',
        digits='Product Unit of Measure',
        readonly=True,
        copy=False,
        help='Net incoming minus outgoing done quantities since the Counting Date.',
    )
    real_time_counted_quantity = fields.Float(
        'Counted Quantity in Real Time',
        digits='Product Unit of Measure',
        copy=False,
        help='The physical quantity reported at the Counting Date.',
    )
    make_it_zero = fields.Boolean('Set Zero')

    state = fields.Selection(
        string='Status', related='stock_inventory_id.state', readonly=True)
    company_id = fields.Many2one(
        'res.company', 'Company', related='stock_inventory_id.company_id',
        index=True, readonly=True, store=True)

    lot_serial = fields.Boolean(string='Serial', compute="compute_lot_serial_require")
    lot_id = fields.Boolean(string='Lot', compute="compute_lot_serial_require")

    # =========================================================================
    # COMPUTE METHODS
    # =========================================================================

    @api.depends(
        'product_id', 'location_id', 'prod_lot_id', 'company_id',
        'quantity_frozen', 'validated_quantity',
        'product_id.stock_quant_ids.quantity',
        'product_id.stock_quant_ids.reserved_quantity',
        'product_id.stock_quant_ids.location_id',
    )
    def _compute_location_quantities(self):
        """Compute on-hand, reserved, and available quantities
        from the specific selected inventoried location only (AC 1, 2, 3, 13, 15).
        """
        for line in self:
            if line.quantity_frozen:
                line.location_quantity = line.validated_quantity
                line.reserved_quantity = 0.0
                line.available_quantity = line.location_quantity
                continue

            if not line.product_id or not line.location_id:
                line.location_quantity = 0.0
                line.reserved_quantity = 0.0
                line.available_quantity = 0.0
                continue

            domain = [
                ('company_id', '=', line.company_id.id),
                ('location_id', '=', line.location_id.id),
                ('product_id', '=', line.product_id.id),
            ]
            if line.prod_lot_id:
                domain.append(('lot_id', '=', line.prod_lot_id.id))

            quants = self.env['stock.quant'].search(domain)
            line.location_quantity = sum(quants.mapped('quantity'))
            line.reserved_quantity = sum(quants.mapped('reserved_quantity'))
            line.available_quantity = line.location_quantity - line.reserved_quantity

    @api.depends('location_quantity', 'quantity_frozen', 'validated_quantity')
    def _compute_quantity(self):
        """On-hand quantity for this inventoried location (AC 1 & 15)."""
        for line in self:
            line.quantity = (
                line.validated_quantity if line.quantity_frozen
                else line.location_quantity
            )

    @api.depends('inventory_quantity', 'location_quantity')
    def _compute_different(self):
        """Different = Counted Quantity - On Hand (AC 6).
        Automatically recalculates when On Hand changes due to stock movements
        or when Counted Quantity is edited (AC 10 & 12).
        """
        for line in self:
            line.different = line.inventory_quantity - line.location_quantity

    @api.depends('different', 'product_id.standard_price')
    def _compute_price_value(self):
        """Diff. Valuation = Different × product unit cost (AC 11 & 12).
        Automatically recalculates whenever Different changes.
        """
        for line in self:
            line.price_value = line.different * line.product_id.standard_price

    def _freeze_onhand_quantity(self):
        lines = self.filtered(lambda line: not line.quantity_frozen)
        lines.invalidate_recordset(['location_quantity', 'quantity'])
        quantities = [(line, line.location_quantity) for line in lines]
        for line, quantity in quantities:
            line.with_context(_freeze_onhand_token=_FREEZE_ONHAND_TOKEN).write({
                'validated_quantity': quantity, 'quantity_frozen': True,
            })

    # =========================================================================
    # CRUD OVERRIDES
    # =========================================================================

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            if {'validated_quantity', 'quantity_frozen', 'quantity', 'different'}.intersection(vals):
                raise UserError(_('On-hand quantities and their snapshots are managed automatically.'))
            # Override client-supplied context defaults as well as explicit values.
            vals.update({'validated_quantity': 0.0, 'quantity_frozen': False})
            # Remove stored-value keys for compute fields that are now non-stored
            vals.pop('location_quantity', None)
            vals.pop('available_quantity', None)
            vals.pop('reserved_quantity', None)
            inventory = self.env['stock.inventory'].browse(
                vals.get('stock_inventory_id', self.env.context.get('default_stock_inventory_id')),
            )
            if inventory.state == 'done':
                raise UserError(_('Lines of a validated inventory cannot be changed.'))
            if inventory.force_counting_date:
                real_time_quantity = (
                    0.0 if vals.get('make_it_zero')
                    else vals.get('real_time_counted_quantity', 0.0)
                )
                vals['real_time_counted_quantity'] = real_time_quantity
                vals['inventory_quantity'] = (
                    real_time_quantity + vals.get('qty_after_counting_date', 0.0)
                )
        return super().create(vals_list)

    def write(self, vals):
        if (any(line.state == 'done' for line in self)
                and self.env.context.get('_freeze_onhand_token') is not _FREEZE_ONHAND_TOKEN):
            raise UserError(_('Lines of a validated inventory cannot be changed.'))
        if (vals.get('stock_inventory_id')
                and self.env['stock.inventory'].browse(vals['stock_inventory_id']).state == 'done'):
            raise UserError(_('Lines of a validated inventory cannot be changed.'))
        if {'quantity', 'different'}.intersection(vals):
            raise UserError(_('On-hand quantity and Different are calculated automatically.'))
        if ({'validated_quantity', 'quantity_frozen'}.intersection(vals)
                and self.env.context.get('_freeze_onhand_token') is not _FREEZE_ONHAND_TOKEN):
            raise UserError(_('Validated on-hand snapshots cannot be edited manually.'))
        formula_fields = {
            'real_time_counted_quantity',
            'qty_after_counting_date',
            'make_it_zero',
        }
        if not formula_fields.intersection(vals):
            return super().write(vals)

        for line in self:
            line_vals = dict(vals)
            if line.force_counting_date:
                make_it_zero = line_vals.get('make_it_zero', line.make_it_zero)
                real_time_quantity = (
                    0.0 if make_it_zero
                    else line_vals.get(
                        'real_time_counted_quantity',
                        line.real_time_counted_quantity,
                    )
                )
                qty_after_counting_date = line_vals.get(
                    'qty_after_counting_date',
                    line.qty_after_counting_date,
                )
                line_vals.update({
                    'real_time_counted_quantity': real_time_quantity,
                    'inventory_quantity': real_time_quantity + qty_after_counting_date,
                })
            super(StockInventoryLine, line).write(line_vals)
        return True

    def unlink(self):
        if any(line.state == 'done' for line in self):
            raise UserError(_('Lines of a validated inventory cannot be changed.'))
        return super().unlink()

    # =========================================================================
    # ONCHANGE & HELPERS
    # =========================================================================

    def _get_qty_after_counting_date(self, counting_date, calculation_date=None):
        """Return the net movement into the inventoried location hierarchy."""
        self.ensure_one()
        if not self.product_id or not self.location_id or not counting_date:
            return 0.0

        calculation_date = calculation_date or fields.Datetime.now()
        domain = [
            ('state', '=', 'done'),
            ('company_id', '=', self.company_id.id),
            ('product_id', '=', self.product_id.id),
            ('date', '>', counting_date),
            ('date', '<=', calculation_date),
            '|',
            ('location_id', 'child_of', self.location_id.id),
            ('location_dest_id', 'child_of', self.location_id.id),
        ]
        if self.prod_lot_id:
            domain.append(('lot_id', '=', self.prod_lot_id.id))

        quantity = 0.0
        for move_line in self.env['stock.move.line'].search(domain):
            source_inside = move_line.location_id._child_of(self.location_id)
            destination_inside = move_line.location_dest_id._child_of(self.location_id)
            if source_inside == destination_inside:
                continue
            move_quantity = move_line.product_uom_id._compute_quantity(
                move_line.quantity,
                self.product_uom_id,
                rounding_method='HALF-UP',
            )
            quantity += move_quantity if destination_inside else -move_quantity
        return quantity

    @api.onchange('real_time_counted_quantity', 'qty_after_counting_date')
    def _onchange_backdated_quantities(self):
        for line in self.filtered('force_counting_date'):
            line.inventory_quantity = (
                line.real_time_counted_quantity + line.qty_after_counting_date
            )

    @api.depends('product_id')
    def compute_lot_serial_require(self):
        for line in self:
            line.lot_serial = False
            if line.product_id.tracking == 'serial':
                line.lot_serial = True
            line.lot_id = False
            if line.product_id.tracking == 'lot':
                line.lot_id = True

    def do_unreserve_product_qty(self):
        move = self.env['stock.move.line'].search([
            ('product_id', '=', self.product_id.id),
            ('location_id', '=', self.stock_inventory_id.location_id.id),
            ('state', 'not in', ('done', 'cancel')),
        ])
        move.mapped('move_id').filtered(
            lambda x: x.state not in ('done', 'cancel')
        )._do_unreserve()

    @api.onchange('product_id', 'prod_lot_id')
    def onchange_product_id(self):
        """When product or lot changes on a new line, pre-fill the counted
        quantity with the current location on-hand (convenience default).
        location_quantity, reserved_quantity, and available_quantity are
        all computed fields and will update automatically.
        """
        for line in self:
            if not line.product_id or not line.location_id:
                continue
            domain = [
                ('company_id', '=', line.stock_inventory_id.company_id.id),
                ('location_id', '=', line.stock_inventory_id.location_id.id),
            ]
            if line.stock_inventory_id and line.stock_inventory_id.partner_id:
                domain.append(('owner_id', '=', line.stock_inventory_id.partner_id.id))
            if line.product_id.tracking == 'none':
                quant_rec = self.env['stock.quant'].search(
                    domain + [('product_id', '=', line.product_id.id)])
                if quant_rec:
                    line.inventory_quantity = sum(quant_rec.mapped('quantity'))
            if line.product_id.tracking != 'none' and line.prod_lot_id:
                quant_rec = self.env['stock.quant'].search(
                    domain + [
                        ('product_id', '=', line.product_id.id),
                        ('lot_id', '=', line.prod_lot_id.id),
                    ])
                if quant_rec:
                    line.inventory_quantity = sum(quant_rec.mapped('quantity'))

    def select_stock_inventory_line(self):
        same_product_records = self.env['stock.inventory.line'].search([
            ('product_id', '=', self.product_id.id),
            ('stock_inventory_id', '=', self.stock_inventory_id.id),
        ])
        selection_record = self.env['inventory.selection'].create({
            'product_id': self.product_id.id,
            'stock_inventory_id': self.stock_inventory_id.id,
        })
        selection_line_id = []
        for line in same_product_records.filtered(lambda x: x.location_quantity):
            selection_line_id.append((0, 0, {
                'inventory_selection_id': selection_record.id,
                'prod_lot_id': line.prod_lot_id.id,
                'quantity_on_hand': line.location_quantity,
                'stock_inventory_line_id': line.id,
                'quantity': 0,
            }))
        if selection_line_id:
            selection_record.write({'selection_line_id': selection_line_id})

        return {
            'name': "Add Products",
            'view_mode': 'form',
            'view_type': 'form',
            'view_id': self.env.ref(
                "inventory_stock_adjustments.inventory_selection_form_view"
            ).id,
            'res_model': 'inventory.selection',
            'res_id': selection_record.id,
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

    @api.onchange('make_it_zero', 'inventory_quantity')
    def onchange_make_zero(self):
        for rec in self:
            if rec.make_it_zero:
                if rec.force_counting_date:
                    rec.real_time_counted_quantity = 0.0
                    rec.inventory_quantity = rec.qty_after_counting_date
                else:
                    rec.inventory_quantity = 0.0
