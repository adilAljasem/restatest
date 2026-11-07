# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import fields, models, api, _
from odoo.exceptions import UserError, ValidationError

_VALIDATE_INVENTORY_TOKEN = object()


class StockMove(models.Model):
    _inherit = "stock.move"

    stock_inventory_id = fields.Many2one('stock.inventory')

    def _get_account_move_line_vals(self):
        """Put the adjustment's analytics on the inventory difference line."""
        vals_list = super()._get_account_move_line_vals()
        distribution = self.stock_inventory_id.analytic_distribution
        if not distribution:
            return vals_list

        adjustment_account = (
            self.location_id.valuation_account_id
            or self.location_dest_id.valuation_account_id
        )
        if not adjustment_account:
            return vals_list

        for vals in vals_list:
            if vals.get('account_id') == adjustment_account.id:
                vals['analytic_distribution'] = dict(distribution)
        return vals_list


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    def _get_inventory_move_values(
            self, qty, location_id, location_dest_id,
            package_id=False, package_dest_id=False):
        values = super()._get_inventory_move_values(
            qty,
            location_id,
            location_dest_id,
            package_id=package_id,
            package_dest_id=package_dest_id,
        )
        inventory_id = self.env.context.get('stock_inventory_id')
        if inventory_id:
            values['stock_inventory_id'] = inventory_id
        return values


class StockInventory(models.Model):
    _name = 'stock.inventory'
    _inherit = ['analytic.mixin']
    _description = 'Stock Inventory Adjustment'
    _order = 'product_id desc'

    name = fields.Char('Reference', required=True,)
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user, required=True, )
    product_id = fields.Many2one(
        'product.product', 'Inventoried Product',
        help="Specify Product to focus your inventory on a particular Product.")
    location_id = fields.Many2one(
        'stock.location', 'Location',
        domain=[('usage', 'in', ('internal', 'transit'))],
        index=True, required=True)
    company_id = fields.Many2one(related='location_id.company_id', string='Company', store=True, readonly=True)
    date = fields.Datetime(
        'Inventory Date',
        readonly=True, required=True,
        default=fields.Datetime.now,
        help="The date that will be used for the stock level check of the products and the validation of the stock move related to this inventory.",)
    lot_id = fields.Many2one(
        'stock.lot', 'Lot Number', index=True, check_company=True)
    package_id = fields.Many2one(
        'stock.package', 'Package',
        domain="[('location_id', '=', location_id)]",
        help='The package containing this quant', ondelete='restrict', check_company=True, index=True)

    state = fields.Selection(string='Status', selection=[
        ('draft', 'Draft'),
        ('cancel', 'Cancelled'),
        ('confirm', 'In Progress'),
        ('wait_for_approval', 'Wait For Approval'),
        ('approved', 'Approved'),
        ('done', 'Validated')],
        copy=False, index=True, readonly=True,
        default='draft')
    filter = fields.Selection(
        string='Inventory of', selection='_selection_filter',
        required=True,
        default='none',
        help="If you do an entire inventory, you can choose 'All Products' and it will prefill the inventory with the current stock.  If you only do some products  "
             "(e.g. Cycle Counting) you can choose 'Manual Selection of Products' and the system won't propose anything.  You can also let the "
             "system propose for a single product / lot /... ")
    add_onhand_zero = fields.Boolean('Include Onhand Zero Product',)
    template_id = fields.Many2one(
        'stock.inventory.template', string='Inventory Template',
        check_company=True, ondelete='restrict',
        help='Load the template products when starting the count, including products with no stock.',
    )
    move_ids = fields.One2many(
        'stock.move', 'stock_inventory_id', string='Created Moves',
        copy=False,
        readonly=True)
    line_ids = fields.One2many(
        'stock.inventory.line', 'stock_inventory_id', string='Inventories',
        copy=False,
    )
    partner_id = fields.Many2one(
        'res.partner', 'Inventoried Owner',
        help="Specify Owner to focus your inventory on a particular Owner.")
    category_id = fields.Many2one(
        'product.category', 'Inventoried Category',
        help="Specify Product Category to focus your inventory on a particular Category.")
    accounting_date = fields.Datetime(
        string='Accounting Date',
        help="Date at which the accounting entries will be created"
            " in case of automated inventory valuation."
            " If empty, the inventory date will be used.",
    )
    force_counting_date = fields.Boolean(
        string='Force Counting Date',
        help='Enable this option when the physical count was performed before '
             'the inventory adjustment was entered in Odoo.',
    )
    counting_date = fields.Datetime(
        string='Counting Date',
        help='The actual date and time at which the physical inventory was counted.',
    )
    sent_for_approval_date = fields.Datetime("Approval Sent Date")
    note = fields.Text(string="Important Note")

    @api.constrains('force_counting_date', 'counting_date')
    def _check_backdated_counting_date(self):
        now = fields.Datetime.now()
        for inventory in self:
            if (inventory.force_counting_date and inventory.counting_date
                    and inventory.counting_date > now):
                raise ValidationError(_('The Counting Date cannot be in the future.'))

    def action_count_with_back_date(self):
        """Recalculate post-count stock movements without reloading the form."""
        self.ensure_one()
        if not self.force_counting_date:
            raise UserError(_('Force Counting Date must be enabled first.'))
        if not self.counting_date:
            raise ValidationError(_(
                'Please enter the Counting Date before calculating the backdated inventory.'
            ))
        if self.counting_date > fields.Datetime.now():
            raise ValidationError(_('The Counting Date cannot be in the future.'))

        calculation_date = fields.Datetime.now()
        for line in self.line_ids:
            line.write({
                'qty_after_counting_date': line._get_qty_after_counting_date(
                    self.counting_date,
                    calculation_date,
                ),
            })

        return {
            'type': 'ir.actions.act_window_close',
            'infos': {'noReload': True},
        }

    def set_product_serial_type_quantity(self):
        for line in self.line_ids.filtered(lambda x: x.product_id.tracking == 'serial'):
            if self.force_counting_date:
                line.real_time_counted_quantity = line.location_quantity
                line.inventory_quantity = line.location_quantity + line.qty_after_counting_date
            else:
                line.inventory_quantity = line.location_quantity

    @api.model
    def _selection_filter(self):
        """ Returns allowed filter options based on 'Settings Warehouse' configurations. """
        res_filter = [
            ('none', ('All products')),
            ('category', ('One product category')),
            ('product', ('One product only')),
            ('partial', ('Select products manually')),
            ('template', _('Template')),
        ]

        if self.env.user.has_group('stock.group_tracking_owner'):
            res_filter += [('owner', ('One owner only')), ('product_owner', ('One product for a specific owner'))]
        if self.env.user.has_group('stock.group_production_lot'):
            res_filter.append(('lot', ('One Lot Number')))
        if self.env.user.has_group('stock.group_tracking_lot'):
            res_filter.append(('pack', ('A Pack')))
        return res_filter

    @api.onchange('filter')
    def onchange_filter(self):
        if self.filter not in ('product', 'product_owner'):
            self.product_id = False
        if self.filter != 'lot':
            self.lot_id = False
        if self.filter not in ('owner', 'product_owner'):
            self.partner_id = False
        if self.filter != 'pack':
            self.package_id = False
        if self.filter != 'category':
            self.category_id = False
        if self.filter != 'template':
            self.template_id = False

    @api.constrains('filter', 'template_id', 'company_id')
    def _check_inventory_template(self):
        for inventory in self:
            if inventory.filter == 'template' and not inventory.template_id:
                raise ValidationError(_('Please select an inventory template.'))
            inventory._check_company(['template_id'])

    @api.constrains('filter', 'product_id', 'lot_id', 'partner_id', 'package_id')
    def _check_filter_product(self):
        if self.filter == 'none' and self.product_id and self.location_id and self.lot_id:
            return
        if self.filter not in ('product', 'product_owner') and self.product_id:
            raise UserError(_('The selected inventory options are not coherent.'))
        if self.filter != 'lot' and self.lot_id:
            raise UserError(_('The selected inventory options are not coherent.'))
        if self.filter not in ('owner', 'product_owner') and self.partner_id:
            raise UserError(_('The selected inventory options are not coherent.'))
        if self.filter != 'pack' and self.package_id:
            raise UserError(_('The selected inventory options are not coherent.'))

    def prepare_inventory_stock_counting(self):
        self.ensure_one()
        if self.filter == 'template':
            return self._prepare_template_stock_counting()
        domain = [
            ('company_id', '=', self.company_id.id),
            ('location_id', '=', self.location_id.id)
        ]
        product = self.env['product.product']
        if self.filter == 'none':
            rec = self.env['stock.quant'].search(domain)
            if self.add_onhand_zero:
                product = product.search([('id', 'not in', rec.mapped('product_id').ids), ('is_storable', '=', True), ('type', '=', 'consu')])
            if rec or product:
                self.prepare_stock_counting_lines(rec, product)
            else:
                raise ValidationError(_('Record not available.'))

        if self.filter == 'category':
            rec = self.env['stock.quant'].search(domain + [('product_id.categ_id', '=', self.category_id.id)])
            if self.add_onhand_zero:
                product = product.search([('categ_id', '=', self.category_id.id), ('id', 'not in', rec.mapped('product_id').ids), ('is_storable', '=', True), ('type', '=', 'consu')])
            if rec or product:
                self.prepare_stock_counting_lines(rec, product)
            else:
                raise ValidationError(_('Record not available.'))

        if self.filter == 'product':
            rec = self.env['stock.quant'].search(domain + [('product_id', '=', self.product_id.id)])
            if self.add_onhand_zero:
                product = product.search([('id', '=', self.product_id.id), ('id', 'not in', rec.mapped('product_id').ids), ('is_storable', '=', True), ('type', '=', 'consu')])
            if rec or product:
                self.prepare_stock_counting_lines(rec, product)
            else:
                raise ValidationError(_('Record not available.'))

        if self.filter == 'lot':
            rec = self.env['stock.quant'].search(domain + [('lot_id', '=', self.lot_id.id)])
            if rec:
                self.prepare_stock_counting_lines(rec)
            else:
                raise ValidationError(_('Record not available.'))

        if self.filter == 'pack':
            rec = self.env['stock.quant'].search(domain + [('package_id', '=', self.package_id.id)])
            if rec:
                self.prepare_stock_counting_lines(rec)
            else:
                raise ValidationError(_('Record not available.'))
        if self.filter == 'partial':
            self.write({'state': 'confirm'})

    def _prepare_template_stock_counting(self):
        """Keep the existing quant/lot and backdated-counting logic for templates."""
        self.ensure_one()
        self.check_access('write')
        if self.state != 'draft':
            raise UserError(_('Counting can only be started on a draft inventory.'))
        if self.line_ids:
            raise UserError(_('Remove existing inventory lines before loading a template.'))
        self._check_inventory_template()
        if not self.template_id.is_active:
            raise UserError(_('Please select an active inventory template.'))
        products = self.template_id.line_ids.product_id
        if not products:
            raise UserError(_('The inventory template must contain at least one product.'))
        if any(not product.active or not product.is_storable or product.type != 'consu'
               for product in products):
            raise UserError(_('All template products must be active storable products.'))
        if any(product.company_id and product.company_id != self.company_id
               for product in products):
            raise UserError(_('Template products must belong to the inventory company.'))
        quants = self.env['stock.quant'].search([
            ('company_id', '=', self.company_id.id),
            ('location_id', '=', self.location_id.id),
            ('product_id', 'in', products.ids),
        ])
        self.prepare_stock_counting_lines(quants, products - quants.product_id)
        if not self.line_ids:
            raise UserError(_('The template contains no products eligible for inventory counting.'))
        return True

    def _check_stock_moves_after_date(self):
        StockMoveLine = self.env['stock.move.line']

        for inv in self:
            error_lines = []
            check_date = inv.sent_for_approval_date or inv.date or fields.Datetime.now()
            for line in inv.line_ids:
                domain = [
                    ("state", "=", "done"),
                    ("product_id", "=", line.product_id.id),
                    ("date", ">", check_date),
                    "|",
                    ("location_id", "=", line.location_id.id),
                    ("location_dest_id", "=", line.location_id.id),
                ]
                if line.prod_lot_id:
                    domain.append(("lot_id", "=", line.prod_lot_id.id))

                move = StockMoveLine.search(domain, limit=1)
                if move:
                    error_lines.append(
                        "- Product: %s | Lot: %s | Location: %s"
                        % (
                            line.product_id.display_name,
                            line.prod_lot_id.name if line.prod_lot_id else "N/A",
                            line.location_id.display_name,
                        )
                    )

            if error_lines:
                raise UserError(
                    _(
                        "Stock moves have occurred after %s for the following inventory lines:\n\n%s"
                        "\n\nPlease cancel the adjustment and create a new one considering the moves."
                    )
                    % (check_date.strftime("%Y-%m-%d %H:%M:%S"), "\n".join(error_lines))
                )

    def prepare_stock_counting_lines(self, rec, product_id=None):
        """Create inventory lines from quants and/or zero-stock products.

        location_quantity, reserved_quantity, and available_quantity are
        now computed fields on stock.inventory.line, so we do NOT pass
        them during creation. inventory_quantity is initialised to the
        quant's current quantity at the selected location only.
        """
        if rec:
            for x in rec:
                bom_ids = x.product_id and x.product_id.product_tmpl_id and 'bom_ids' in x.product_id.product_tmpl_id and x.product_id.product_tmpl_id.bom_ids
                if not bom_ids or bom_ids.filtered(lambda bom: bom.type != 'phantom'):
                    self.write({
                        'line_ids': [(0, 0, {
                            'product_id': x.product_id.id,
                            'product_uom_id': x.product_uom_id.id,
                            'prod_lot_id': x.lot_id.id,
                            'package_id': x.package_id.id,
                            'inventory_quantity': x.quantity,
                        })],
                        'state': 'confirm',
                        'date': fields.Datetime.now(),
                    })
        if product_id:
            for x in product_id:
                bom_ids = x.product_tmpl_id and 'bom_ids' in x.product_tmpl_id and x.product_tmpl_id.bom_ids
                if not bom_ids or bom_ids.filtered(lambda bom: bom.type != 'phantom'):
                    self.write({
                        'state': 'confirm',
                        'line_ids': [(0, 0, {
                            'product_id': x.id,
                            'product_uom_id': x.uom_id.id,
                        })],
                    })

    def action_send_for_approval(self):
        self.ensure_one()
        if self.force_counting_date and not self.counting_date:
            raise ValidationError(_(
                'Please enter the Counting Date before calculating the backdated inventory.'
            ))

        has_group = self.env.ref('inventory_stock_adjustments.group_stock_adjustments_manager')

        url = self._get_html_link(title=self.name)

        users = self.env['res.users']
        if has_group:
            users = has_group.user_ids

        message = _(
            "%s has requested approval for stock changes. Review it here: %s" % (self.env.user.name, url),
        )
        for user in users:
            if self.env.user.partner_id.id != user.partner_id.id:
                channel_id = self.env['discuss.channel']._get_or_create_chat([self.env.user.partner_id.id, user.partner_id.id])
                channel_id.message_post(
                    body=Markup(message),
                    message_type='notification',
                    partner_ids=[user.partner_id.id],
                )
        self.write({'state': 'wait_for_approval', 'sent_for_approval_date': fields.Datetime.now()})

    def action_approved(self):
        url = self._get_html_link(title=self.name)

        message = _(
            "Your stock changes have been approved by %s. You can view them here: %s" % (self.env.user.name, url),
        )
        if self.env.user.partner_id.id != self.user_id.partner_id.id:
            channel_id = self.env['discuss.channel']._get_or_create_chat([self.env.user.partner_id.id, self.user_id.partner_id.id])
            channel_id.message_post(
                body=Markup(message),
                message_type='notification',
                partner_ids=[self.user_id.partner_id.id],
            )
        self.write({'state': 'approved'})

    def action_done(self):
        self.ensure_one()
        if self.state == 'done':
            return True
        self.line_ids._freeze_onhand_quantity()
        inventory_move_date = (
            self.counting_date if self.force_counting_date else False
        )
        inventory_context = {'stock_inventory_id': self.id}
        if self.accounting_date:
            inventory_context['force_period_date'] = fields.Date.context_today(
                self, timestamp=self.accounting_date,
            )
        quants_to_apply = self.env['stock.quant']
        for line in self.line_ids:
            domain = [
                ('company_id', '=', line.stock_inventory_id.company_id.id),
                ('location_id', '=', line.stock_inventory_id.location_id.id),
                ('owner_id', '=', line.stock_inventory_id.partner_id.id),
            ]
            if line.product_id.tracking in ['lot', 'serial'] and not line.prod_lot_id:
                raise UserError('Please Add serial number for this product %s' % line.product_id.display_name)

            quant_rec = self.env['stock.quant'].search(domain + [('product_id', '=', line.product_id.id)])
            if line.prod_lot_id:
                quant_rec = self.env['stock.quant'].search(domain + [('product_id', '=', line.product_id.id), ('lot_id', '=', line.prod_lot_id.id)])

            for validate in quant_rec:
                inventory_quant = validate.with_context(**inventory_context)
                if line.make_it_zero and not self.force_counting_date:
                    inventory_quant.write({'inventory_quantity': 0.0})
                    quants_to_apply |= validate
                    line.inventory_quantity = 0.0
                elif validate.quantity != line.inventory_quantity:
                    inventory_quant.write({'inventory_quantity': line.inventory_quantity})
                    quants_to_apply |= validate

            if not quant_rec:
                stock_quant = self.env['stock.quant'].with_context(
                    **inventory_context
                ).create({
                    'product_id': line.product_id.id,
                    'location_id': line.location_id.id,
                    'inventory_quantity': line.inventory_quantity,
                    'lot_id': line.prod_lot_id.id,
                })
                quants_to_apply |= stock_quant

        if quants_to_apply:
            quants_to_apply.with_context(**inventory_context).action_apply_inventory(
                date=inventory_move_date,
            )

        account_moves = self.move_ids.sudo().account_move_id
        if account_moves:
            account_moves.write({'ref': self.name})

        if inventory_move_date:
            # Odoo applies ``date`` to the completed stock move, while the
            # generated move line keeps the execution time unless aligned here.
            self.move_ids.write({'date': inventory_move_date})
            self.move_ids.move_line_ids.write({'date': inventory_move_date})
        self.with_context(_validate_inventory_token=_VALIDATE_INVENTORY_TOKEN).write({
            'state': 'done',
            'accounting_date': self.accounting_date or fields.Datetime.now(),
        })

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get('state', self.env.context.get('default_state')) == 'done' for vals in vals_list):
            raise UserError(_('Use Validate to complete an inventory.'))
        return super().create(vals_list)

    def write(self, vals):
        if ('state' in vals and vals['state'] != 'done'
                and any(inventory.state == 'done' for inventory in self)):
            raise UserError(_('A validated inventory cannot be reopened. Create a new inventory instead.'))
        if vals.get('state') == 'done':
            if (any(inventory.state != 'done' for inventory in self)
                    and self.env.context.get('_validate_inventory_token') is not _VALIDATE_INVENTORY_TOKEN):
                raise UserError(_('Use Validate to complete an inventory.'))
            self.line_ids._freeze_onhand_quantity()
        return super().write(vals)

    def action_cancel_draft(self):
        self.write({
            'line_ids': [(5,)],
            'state': 'draft'
        })

    def action_export_stock_adjustment(self):
        return {
            'name': "Export Stock Adjustment",
            'view_mode': 'form',
            'view_id': self.env.ref("inventory_stock_adjustments.stock_adjustment_report_export_view_form").id,
            'res_model': 'stock.adjustment.report.export',
            'type': 'ir.actions.act_window',
            'target': 'new',
        }
