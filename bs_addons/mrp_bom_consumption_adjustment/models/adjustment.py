from collections import defaultdict
from datetime import datetime, time, timedelta
import json

from odoo import api, fields, models, Command, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare, float_is_zero


# An RPC caller cannot manufacture this capability by supplying a context value.
_INTERNAL = object()


def internal(record):
    return record.with_context(recipe_internal=_INTERNAL)


def empty_relation_commands(value):
    """The web client sends empty x2many values when saving a new form."""
    if value is False or value is None or value == []:
        return True
    return isinstance(value, (list, tuple)) and all(
        isinstance(command, (list, tuple)) and command and (
            command[0] == Command.CLEAR or
            command[0] == Command.SET and len(command) > 2 and not command[2]
        ) for command in value
    )


class ProductCategory(models.Model):
    _inherit = 'product.category'

    recipe_adjustment_location_id = fields.Many2one(
        'stock.location', string='Recipe Adjustment Location', company_dependent=True,
        domain="[('usage', '=', 'inventory'), '|', ('company_id', '=', False), ('company_id', '=', allowed_company_ids[0])]",
        help='Inventory loss location for consumption differences of this component category.')

    @api.constrains('recipe_adjustment_location_id')
    def _check_recipe_location(self):
        for category in self:
            location = category.recipe_adjustment_location_id
            if location and (location.usage != 'inventory' or
                             location.company_id and location.company_id != self.env.company):
                raise ValidationError(_('Choose an inventory loss location in the active company or a shared location.'))


class Adjustment(models.Model):
    _name = 'mrp.bom.consumption.adjustment'
    _description = 'BOM Consumption Adjustment'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _check_company_auto = True
    _order = 'id desc'

    name = fields.Char(string='Reference', default='New', readonly=True, copy=False, required=True)
    state = fields.Selection([('draft', 'Draft'), ('reviewed', 'Reviewed'),
                              ('confirmed', 'Confirmed'), ('cancelled', 'Cancelled')],
                             default='draft', readonly=True, tracking=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date(default=fields.Date.context_today, required=True)
    bom_id = fields.Many2one('mrp.bom', string='BOM', required=True, check_company=True)
    bom_type = fields.Selection(related='bom_id.type', string='BOM Type')
    product_tmpl_id = fields.Many2one(related='bom_id.product_tmpl_id', string='Product')
    product_id = fields.Many2one(related='bom_id.product_id', string='Product Variant')
    bom_quantity = fields.Float(readonly=True, digits='Product Unit')
    bom_uom_id = fields.Many2one('uom.uom', readonly=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    warehouse_id = fields.Many2one('stock.warehouse', required=True, check_company=True)
    location_id = fields.Many2one('stock.location', string='Stock Location', required=True,
                                 check_company=True, domain="[('usage', '=', 'internal')]")
    analytic_account_id = fields.Many2one('account.analytic.account', check_company=True)
    notes = fields.Text(string='Reason')
    line_ids = fields.One2many('mrp.bom.consumption.line', 'adjustment_id', copy=False)
    detail_ids = fields.One2many('mrp.bom.consumption.detail', 'adjustment_id', copy=False)
    source_ids = fields.Many2many('mrp.bom.consumption.source', copy=False, readonly=True)
    move_ids = fields.One2many('stock.move', 'recipe_document_id', readonly=True)
    account_move_id = fields.Many2one('account.move', readonly=True, copy=False, check_company=True)
    review_signature = fields.Text(readonly=True, copy=False)
    review_warning = fields.Text(readonly=True, copy=False)

    def _manager(self):
        if not self.env.user.has_group('mrp.group_mrp_manager'):
            raise AccessError(_('Only manufacturing administrators can execute adjustments.'))

    @api.model_create_multi
    def create(self, vals_list):
        self._manager()
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            if vals.get('state', 'draft') != 'draft':
                raise AccessError(_('Review and confirm using the document buttons.'))
            for field in ('account_move_id', 'review_signature', 'review_warning'):
                if vals.get(field):
                    raise AccessError(_('Review and confirm using the document buttons.'))
                vals[field] = False
            for field in ('source_ids', 'detail_ids', 'move_ids'):
                if not empty_relation_commands(vals.get(field)):
                    raise AccessError(_('Review and confirm using the document buttons.'))
                vals.pop(field, None)
            bom = self.env['mrp.bom'].browse(vals.get('bom_id'))
            # Readonly onchange fields are not necessarily submitted by the web
            # client. Persist authoritative snapshots even when it sends lines.
            vals.update(state='draft', bom_quantity=bom.product_qty,
                        bom_uom_id=bom.product_uom_id.id)
            vals['name'] = self.env['ir.sequence'].with_company(
                vals.get('company_id', self.env.company.id)).next_by_code('mrp.bom.consumption.adjustment') or 'New'
        records = super().create(vals_list)
        for record in records.filtered(lambda r: not r.line_ids):
            record._load_lines()
        return records

    def write(self, vals):
        vals = dict(vals)
        if self.env.context.get('recipe_internal') is not _INTERNAL:
            self._manager()
            # Ignore unchanged generated values/empty form fields; reject actual
            # mutation of generated results, including nonempty x2many commands.
            for field in ('source_ids', 'detail_ids', 'move_ids'):
                if field in vals and empty_relation_commands(vals[field]) and not any(self.mapped(field)):
                    vals.pop(field)
            for field in ('state', 'name', 'account_move_id', 'review_signature',
                          'review_warning', 'bom_quantity', 'bom_uom_id'):
                if field in vals and all(
                        (record[field].id if self._fields[field].type == 'many2one' else record[field]) == vals[field]
                        for record in self):
                    vals.pop(field)
            if not vals:
                return True
            if any(r.state == 'confirmed' for r in self) and set(vals) - {'message_follower_ids', 'message_partner_ids'}:
                raise UserError(_('Confirmed adjustments are read-only.'))
            if set(vals) & {'state', 'name', 'account_move_id', 'source_ids', 'detail_ids',
                            'review_signature', 'review_warning', 'move_ids', 'bom_quantity', 'bom_uom_id'}:
                raise AccessError(_('Use the document buttons to change generated data.'))
            if any(r.state in ('reviewed', 'cancelled') for r in self):
                raise UserError(_('Reset to draft before editing a reviewed adjustment.'))
            if 'bom_id' in vals:
                bom = self.env['mrp.bom'].browse(vals['bom_id'])
                vals.update(bom_quantity=bom.product_qty, bom_uom_id=bom.product_uom_id.id)
        result = super().write(vals)
        if 'bom_id' in vals and 'line_ids' not in vals:
            for record in self:
                record._load_lines()
        return result

    def unlink(self):
        self._manager()
        if any(r.state != 'draft' for r in self):
            raise UserError(_('Only draft adjustments can be deleted.'))
        return super().unlink()

    @api.onchange('warehouse_id')
    def _onchange_warehouse(self):
        self.location_id = self.warehouse_id.lot_stock_id

    @api.onchange('bom_id')
    def _onchange_bom(self):
        self.bom_quantity = self.bom_id.product_qty
        self.bom_uom_id = self.bom_id.product_uom_id
        self.line_ids = [Command.clear()] + [Command.create(self._line_values(line)) for line in self.bom_id.bom_line_ids]

    def _line_values(self, line):
        return {'bom_line_id': line.id, 'product_id': line.product_id.id,
                'uom_id': line.product_uom_id.id, 'old_quantity': line.product_qty,
                'new_quantity': line.product_qty}

    def _load_lines(self):
        internal(self).write({'bom_quantity': self.bom_id.product_qty,
                              'bom_uom_id': self.bom_id.product_uom_id.id,
                              'line_ids': [Command.clear()] + [Command.create(self._line_values(line))
                                                             for line in self.bom_id.bom_line_ids]})

    @api.constrains('date_from', 'date_to', 'location_id', 'warehouse_id', 'bom_id', 'company_id')
    def _check_scope(self):
        for record in self:
            if record.date_from and record.date_to and record.date_from > record.date_to:
                raise ValidationError(_('Date From must not be after Date To.'))
            if record.bom_id.company_id and record.bom_id.company_id != record.company_id:
                raise ValidationError(_('The BOM must belong to the adjustment company.'))
            if record.location_id and (record.location_id.usage != 'internal' or not
                    self.env['stock.location'].search_count([('id', '=', record.location_id.id),
                        ('id', 'child_of', record.warehouse_id.view_location_id.id)])):
                raise ValidationError(_('The stock location must be internal and inside the warehouse.'))

    def _lock(self):
        self.ensure_one()
        # Serialize overlapping documents for a BOM, including across companies
        # for shared BOMs. All state and prior adjustment reads follow the lock.
        self.env.cr.execute('SELECT id FROM mrp_bom WHERE id = %s FOR UPDATE', [self.bom_id.id])
        self.env.cr.execute('SELECT id FROM mrp_bom_consumption_adjustment WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        self.bom_id.invalidate_recordset()
        self.bom_id.bom_line_ids.invalidate_recordset()

    def action_draft(self):
        self._manager()
        for record in self:
            record._lock()
            if record.state not in ('reviewed', 'cancelled'):
                raise UserError(_('Only reviewed or cancelled adjustments can be reset to draft.'))
            internal(record).write({'state': 'draft', 'review_signature': False,
                                    'review_warning': False, 'detail_ids': [Command.clear()]})
        return True

    def action_cancel(self):
        self._manager()
        for record in self:
            record._lock()
            if record.state not in ('draft', 'reviewed'):
                raise UserError(_('Only draft or reviewed adjustments can be cancelled.'))
            internal(record).write({'state': 'cancelled', 'review_signature': False})
        return True

    def _leaves(self, product, selected_line, new=False):
        """Explode phantom children only; preserve stocked manufactured products.

        Coefficients are in leaf stock UoM per one root BOM batch. A path retains
        the selected top-level line, even for multi-level kits.
        """
        result = []

        def walk(line, quantity, parent_product, path):
            if line._skip_bom_line(parent_product):
                return
            if line.id in path:
                raise UserError(_('Recursive kit structure.'))
            child = self.env['mrp.bom'].with_company(self.company_id)._bom_find(
                line.product_id, company_id=self.company_id.id, bom_type='phantom')[line.product_id]
            if child:
                factor = line.product_uom_id._compute_quantity(
                    quantity, child.product_uom_id, round=False) / child.product_qty
                for subline in child.bom_line_ids:
                    walk(subline, subline.product_qty * factor, line.product_id, path + [line.id])
            else:
                result.append((line, line.product_uom_id._compute_quantity(
                    quantity, line.product_id.uom_id, round=False)))

        walk(selected_line.bom_line_id,
             selected_line.new_quantity if new else selected_line.old_quantity, product, [])
        return result

    def _dates_domain(self, field='date'):
        # Odoo datetimes are UTC. User dates cover complete days in the user's timezone.
        import pytz
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        start = tz.localize(datetime.combine(self.date_from, time.min)).astimezone(pytz.UTC).replace(tzinfo=None)
        end = tz.localize(datetime.combine(self.date_to + timedelta(days=1), time.min)).astimezone(pytz.UTC).replace(tzinfo=None)
        return [(field, '>=', start), (field, '<', end)]

    def _source(self, key, moves, product, quantity, basis, needs_review=False, production=False, unbuild=False):
        Source = self.env['mrp.bom.consumption.source']
        source = Source.search([('key', '=', key), ('company_id', '=', self.company_id.id)], limit=1)
        vals = {'key': key, 'company_id': self.company_id.id, 'bom_id': self.bom_id.id,
                'product_id': product.id, 'move_ids': [Command.set(moves.ids)],
                'uom_id': self.bom_uom_id.id,
                'production_id': production.id if production else False,
                'unbuild_id': unbuild.id if unbuild else False,
                'picking_id': moves[:1].picking_id.id,
                'execution_date': production.date_finished if production else max(moves.mapped('date')),
                'quantity': quantity, 'basis': basis, 'needs_review': needs_review}
        if not source:
            source = internal(Source).create(vals)
        else:
            # Never change the historical basis after its first review. Detect
            # changed execution data rather than silently accepting stale sources.
            if set(source.move_ids.ids) != set(moves.ids):
                if self.state != 'draft':
                    raise UserError(_('Source movements changed: %s. Review the execution basis.', source.display_name))
                internal(source).write({'move_ids': [Command.set(moves.ids)]})
            stored_quantity = source.uom_id._compute_quantity(source.quantity, self.bom_uom_id, round=False)
            if (production or unbuild) and float_compare(stored_quantity, quantity, precision_digits=8):
                raise UserError(_('Execution quantity changed since the first review: %s', (production or unbuild).display_name))
        return source

    def _kit_move_product(self, move):
        """Resolve root provenance; shared leaf BOM lines are never identity."""
        products = self.bom_id.product_id or self.bom_id.product_tmpl_id.product_variant_ids
        current, seen = move, set()
        identified = self.env['product.product']
        while current and current.id not in seen:
            seen.add(current.id)
            if current.recipe_root_bom_id:
                if current.recipe_root_bom_id != self.bom_id:
                    return self.env['product.product']
                identified |= current.recipe_root_product_id
            if 'sale_line_id' in current._fields and current.sale_line_id:
                identified |= current.sale_line_id.product_id
            current = current.origin_returned_move_id
        if len(identified) != 1 or identified not in products:
            return self.env['product.product']
        return identified

    def _check_kit_sources(self, sources):
        if self.bom_id.type == 'normal':
            return
        for source in sources:
            if source.bom_id != self.bom_id or not source.move_ids or any(
                    self._kit_move_product(move) != source.product_id
                    for move in source.move_ids):
                raise UserError(_('Execution source does not belong to the selected kit. Reset to draft and Review again.'))

    def _collect_sources(self):
        self.ensure_one()
        Move = self.env['stock.move']
        sources = self.env['mrp.bom.consumption.source']
        if self.bom_id.type == 'normal':
            productions = self.env['mrp.production'].search([
                ('state', '=', 'done'), ('bom_id', '=', self.bom_id.id),
                ('company_id', '=', self.company_id.id),
                ('picking_type_id.warehouse_id', '=', self.warehouse_id.id),
            ] + self._dates_domain('date_finished'))
            for production in productions:
                moves = production.move_raw_ids.filtered(lambda m: m.state == 'done' and not m.recipe_adjustment_line_id)
                # Include inverse moves (e.g. component returns) linked by origin,
                # without treating them as another production execution.
                frontier = moves
                while frontier:
                    frontier = Move.search([('origin_returned_move_id', 'in', frontier.ids),
                        ('state', '=', 'done'), ('unbuild_id', '=', False),
                        ('recipe_adjustment_line_id', '=', False),
                        ('company_id', '=', self.company_id.id)] + self._dates_domain()) - moves
                    moves |= frontier
                quantity = production.product_uom_id._compute_quantity(
                    production.qty_produced, self.bom_uom_id, round=False)
                sources |= self._source('mo:%s' % production.id, moves, production.product_id,
                    quantity, _('Actual completed production quantity'), production=production)
            unbuilds = self.env['mrp.unbuild'].search([
                ('state', '=', 'done'), ('bom_id', '=', self.bom_id.id),
                ('mo_id', 'in', productions.ids),
                ('company_id', '=', self.company_id.id),
                ('location_dest_id', 'child_of', self.warehouse_id.view_location_id.id),
            ] + self._dates_domain('produce_line_ids.date'))
            for unbuild in unbuilds:
                moves = unbuild.produce_line_ids.filtered(lambda m: m.state == 'done' and not m.recipe_adjustment_line_id)
                quantity = -unbuild.product_uom_id._compute_quantity(
                    unbuild.product_qty, self.bom_uom_id, round=False)
                sources |= self._source('unbuild:%s' % unbuild.id, moves, unbuild.product_id,
                    quantity, _('Actual completed unbuild quantity (negative production)'), unbuild=unbuild)
        else:
            # Nonstandard BOM types use their actual component moves as a
            # source. Without a native frozen coefficient their execution basis
            # requires explicit verification, just like historical kits.
            products = self.bom_id.product_id or self.bom_id.product_tmpl_id.product_variant_ids
            leaf_ids = set()
            for product in products:
                for line in self.line_ids:
                    leaf_ids.update(leaf.id for leaf, qty in self._leaves(product, line))
            moves = Move.search([
                ('state', '=', 'done'), ('company_id', '=', self.company_id.id),
                ('recipe_adjustment_line_id', '=', False),
                '|', ('recipe_root_bom_id', '=', self.bom_id.id), ('bom_line_id', 'in', list(leaf_ids)),
            ] + self._dates_domain())
            groups = defaultdict(lambda: Move)
            warehouse_locations = self.env['stock.location'].search([
                ('id', 'child_of', self.warehouse_id.view_location_id.id), ('usage', '=', 'internal')])
            for move in moves:
                if not self._kit_move_product(move):
                    continue
                outgoing = move.location_id in warehouse_locations and move.location_dest_id.usage == 'customer'
                incoming = move.location_dest_id in warehouse_locations and move.location_id.usage == 'customer'
                origin = move.origin_returned_move_id
                if not outgoing and not (incoming and origin and origin.state == 'done'
                        and origin.company_id == self.company_id
                        and origin.location_id in warehouse_locations
                        and origin.location_dest_id.usage == 'customer'
                        and not origin.recipe_adjustment_line_id
                        and (origin.recipe_root_bom_id == self.bom_id
                             or origin._recipe_original_bom_line().id in leaf_ids)):
                    continue
                if (move.location_id in warehouse_locations) == (move.location_dest_id in warehouse_locations):
                    continue  # internal transfers never consume an additional kit
                token = move.recipe_execution_token or 'legacy'
                sale = move.sale_line_id.id if 'sale_line_id' in move._fields else 0
                key = 'kit:%s:%s:%s:%s:%s' % (self.bom_id.id, move.picking_id.id,
                    move.raw_material_production_id.id, sale, token)
                groups[key] |= move
            for key, group in groups.items():
                product = self._kit_move_product(group[:1])
                if any(self._kit_move_product(move) != product for move in group):
                    raise UserError(_('Execution source contains movements from different kit products.'))
                ratios = defaultdict(float)
                expected = set(group[:1].recipe_expected_leaves or [])
                reliable = (bool(expected) and expected == set(group.bom_line_id.ids) and
                    all(m.recipe_quantity_per_unit and m.recipe_root_uom_id and m.recipe_leaf_uom_id and m.recipe_root_bom_id == self.bom_id for m in group))
                for move in group:
                    if move.recipe_quantity_per_unit:
                        sign = 1 if move.location_id in warehouse_locations else -1
                        root_qty = move.product_uom._compute_quantity(
                            move.quantity, move.recipe_leaf_uom_id or move.bom_line_id.product_uom_id, round=False) / move.recipe_quantity_per_unit
                        if move.recipe_root_uom_id:
                            root_qty = move.recipe_root_uom_id._compute_quantity(root_qty, self.bom_uom_id, round=False)
                        ratios[move.bom_line_id.id] += sign * root_qty
                quantities = list(ratios.values())
                reliable = reliable and bool(quantities) and all(abs(q - quantities[0]) < 1e-7 for q in quantities)
                quantity = quantities[0] if reliable else 0
                sources |= self._source(key, group, product, quantity,
                    _('Frozen kit explosion coefficients; actual executed component quantities') if reliable else
                    _('Historical or partial kit: enter the verified executed quantity and explain its evidence.'),
                    needs_review=not reliable)
        return sources

    def _calculate(self, sources):
        details, signature = [], []
        chosen = self.line_ids.filtered('apply_change')
        for source in sources.sorted('id'):
            if source.needs_review:
                continue
            executed_quantity = source.uom_id._compute_quantity(source.quantity, self.bom_uom_id, round=False)
            for line in chosen:
                old_leaves = self._leaves(source.product_id, line)
                new_leaves = self._leaves(source.product_id, line, new=True)
                if [leaf.id for leaf, qty in old_leaves] != [leaf.id for leaf, qty in new_leaves]:
                    raise UserError(_('The exploded BOM structure changed. Reload the BOM.'))
                for leaf, coefficient in new_leaves:
                    if any(m.product_id == leaf.product_id and not m._recipe_original_bom_line() for m in source.move_ids):
                        raise UserError(_('Source %s contains component movements without a BOM line link. Resolve their provenance before review.', source.display_name))
                    # Odoo loses the parent path on merged moves; refuse ambiguous
                    # branches rather than counting the same consumption twice.
                    occurrences = sum(sum(l.id == leaf.id for l, q in self._leaves(source.product_id, other))
                                      for other in self.line_ids)
                    if occurrences != 1:
                        raise UserError(_('Component line %s occurs through multiple kit paths; separate the BOM paths before adjustment.', leaf.display_name))
                    relevant = source.move_ids.filtered(lambda m: m._recipe_original_bom_line() == leaf and m.product_id == leaf.product_id)
                    recorded = 0
                    for move in relevant:
                        # Internal -> production/customer is consumption; its
                        # reverse returns stock. Intermediate internal legs = 0.
                        sign = int(move.location_id.usage == 'internal') - int(move.location_dest_id.usage == 'internal')
                        recorded += sign * move.product_uom._compute_quantity(move.quantity, leaf.product_id.uom_id, round=False)
                    required = executed_quantity / self.bom_quantity * coefficient
                    previous = self.env['mrp.bom.consumption.detail'].search([
                        ('source_id', '=', source.id), ('bom_line_id', '=', line.bom_line_id.id),
                        ('leaf_bom_line_id', '=', leaf.id), ('product_id', '=', leaf.product_id.id),
                        ('adjustment_id.state', '=', 'confirmed')])
                    prior = sum(previous.mapped('adjustment_quantity'))
                    recorded += prior
                    delta = leaf.product_id.uom_id.round(required - recorded)
                    cost = leaf.product_id.with_company(self.company_id).standard_price
                    vals = {'line_id': line.id, 'source_id': source.id,
                        'bom_line_id': line.bom_line_id.id, 'leaf_bom_line_id': leaf.id,
                        'product_id': leaf.product_id.id, 'uom_id': leaf.product_id.uom_id.id,
                        'executed_quantity': executed_quantity, 'recorded_quantity': recorded,
                        'required_quantity': required, 'previous_quantity': prior,
                        'adjustment_quantity': delta, 'unit_cost': cost,
                        'adjustment_value': delta * cost,
                        'original_move_ids': [Command.set(relevant.ids)]}
                    details.append(vals)
                    signature.append((source.id, source.quantity, line.id, leaf.id, coefficient,
                                      recorded, required, prior, delta,
                                      [(m.id, m.quantity, str(m.date), m.location_id.id, m.location_dest_id.id) for m in relevant]))
        return details, json.dumps(signature, sort_keys=True)

    def _check_bom_snapshot(self):
        if set(self.line_ids.bom_line_id.ids) != set(self.bom_id.bom_line_ids.ids) or len(self.line_ids) != len(self.bom_id.bom_line_ids):
            raise UserError(_('BOM component lines changed. Select the BOM again to reload them.'))
        if not self.bom_quantity or self.bom_quantity != self.bom_id.product_qty or self.bom_uom_id != self.bom_id.product_uom_id:
            raise UserError(_('BOM base quantity changed. Select the BOM again to reload its quantities.'))
        for line in self.line_ids:
            if (line.bom_line_id.bom_id != self.bom_id or line.bom_line_id.product_id != line.product_id
                    or line.bom_line_id.product_uom_id != line.uom_id
                    or line.old_quantity != line.bom_line_id.product_qty):
                raise UserError(_('BOM components changed. Select the BOM again to reload them.'))

    def action_review(self):
        self._manager()
        for record in self:
            record._lock()
            if record.state != 'draft':
                raise UserError(_('Only draft adjustments can be reviewed.'))
            record._check_bom_snapshot()
            if not record.line_ids.filtered('apply_change'):
                raise UserError(_('Select at least one component to change.'))
            sources = record.with_company(record.company_id)._collect_sources()
            vals, signature = record._calculate(sources)
            warnings = []
            if not sources:
                warnings.append(_('No completed executions were found in this period.'))
            if sources.filtered('needs_review'):
                warnings.append(_('Some execution quantities require evidence. Open Execution Sources, enter verified quantities and evidence, validate them, then Review again.'))
            internal(record).write({'source_ids': [Command.set(sources.ids)],
                'detail_ids': [Command.clear()] + [Command.create(v) for v in vals],
                'review_signature': signature, 'review_warning': '\n'.join(warnings),
                'state': 'draft' if warnings else 'reviewed'})
        return True

    def action_confirm(self):
        self._manager()
        self.ensure_one()
        self._lock()
        if self.state != 'reviewed':
            raise UserError(_('Review the adjustment before confirming.'))
        self._check_bom_snapshot()
        self._check_kit_sources(self.source_ids)
        # Re-read all execution data and prior corrections under the BOM lock.
        sources = self.with_company(self.company_id)._collect_sources()
        self._check_kit_sources(sources)
        vals, signature = self._calculate(sources)
        if (set(sources.ids) != set(self.source_ids.ids) or sources.filtered('needs_review') or signature != self.review_signature):
            raise UserError(_('Sources or previous corrections changed. Reset to draft and Review again.'))
        with self.env.cr.savepoint():
            self._post_adjustment()
        return True

    def _post_adjustment(self):
        self = self.with_company(self.company_id).with_context(allowed_company_ids=self.company_id.ids)
        journal = self.company_id.sudo().account_stock_journal_id
        if not journal or journal.company_id != self.company_id:
            raise UserError(_('Configure the company stock journal.'))
        account_lines = []
        all_moves = self.env['stock.move']
        # Capture all costs before any move changes AVCO/FIFO standard prices.
        products = self.detail_ids.product_id.sorted('id')
        if products:
            self.env.cr.execute('SELECT id FROM product_product WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(products.ids)])
            products.invalidate_recordset(['standard_price'])
        costs = {p.id: p.with_company(self.company_id).standard_price for p in products}
        for detail in self.detail_ids:
            cost = costs[detail.product_id.id]
            internal(detail).write({'unit_cost': cost,
                                    'adjustment_value': detail.adjustment_quantity * cost})
        # Net linked deliveries/returns before posting. Keep each source's
        # correction in details so overlapping reviews remain idempotent.
        for details in self.detail_ids.grouped(lambda d: (d.line_id.id, d.product_id.id)).values():
            detail = details[:1]
            product = detail.product_id.with_company(self.company_id)
            quantity = sum(details.mapped('adjustment_quantity'))
            cost = costs[product.id]
            if float_is_zero(quantity, precision_rounding=product.uom_id.rounding):
                continue
            location = product.categ_id.with_company(self.company_id).recipe_adjustment_location_id
            valuation = product._get_product_accounts().get('stock_valuation')
            difference = location.valuation_account_id
            if (not product.is_storable or product.valuation != 'real_time' or cost < 0 or not location
                    or location.usage != 'inventory' or location.company_id and location.company_id != self.company_id
                    or not valuation or not difference or valuation == difference):
                raise UserError(_('Configure automated valuation, the stock valuation account, and a distinct recipe loss account/location for %s.', product.display_name))
            allocations = details.allocation_ids
            if product.tracking != 'none':
                if product.uom_id.compare(sum(allocations.mapped('quantity')), abs(quantity)):
                    raise UserError(_('Allocate the entire adjustment quantity to lots/serials for %s in Review Details.', product.display_name))
                if any(a.lot_id.product_id != product or a.quantity <= 0 or
                       a.lot_id.company_id and a.lot_id.company_id != self.company_id for a in allocations):
                    raise UserError(_('Invalid lot allocation for %s.', product.display_name))
                if product.tracking == 'serial' and (len(allocations.lot_id) != len(allocations) or
                        any(product.uom_id.compare(a.quantity, 1) for a in allocations)):
                    raise UserError(_('Each serial number must be allocated exactly one unit.'))
            for account in valuation | difference:
                if self.company_id not in account.company_ids or not account.active:
                    raise UserError(_('An adjustment account is unavailable in this company: %s', account.display_name))
            outgoing = quantity > 0
            fifo_value_before = 0
            if product.cost_method == 'fifo':
                if self.date != fields.Date.context_today(self):
                    raise UserError(_('FIFO adjustments must be posted today to preserve historical outgoing valuations. The source period can be historical.'))
                product.invalidate_recordset(['total_value', 'avg_cost'])
                fifo_value_before = product.total_value
            move = internal(self.env['stock.move']).create({
                'origin': self.name, 'company_id': self.company_id.id,
                'product_id': product.id, 'product_uom': product.uom_id.id,
                'product_uom_qty': abs(quantity),
                'location_id': (self.location_id if outgoing else location).id,
                'location_dest_id': (location if outgoing else self.location_id).id,
                'recipe_adjustment_line_id': detail.line_id.id,
                'recipe_frozen_cost': cost, 'recipe_source_ids': [Command.set(details.source_id.ids)],
            })
            move._action_confirm(merge=False)
            if product.tracking != 'none':
                for allocation in allocations:
                    internal(self.env['stock.move.line']).create({
                        'move_id': move.id, 'product_id': product.id,
                        'product_uom_id': product.uom_id.id, 'quantity': allocation.quantity,
                        'lot_id': allocation.lot_id.id, 'company_id': self.company_id.id,
                        'location_id': move.location_id.id, 'location_dest_id': move.location_dest_id.id})
            else:
                move.quantity = abs(quantity)
            move.picked = True
            move._action_done()
            if move.account_move_id:
                raise UserError(_('A stock extension created an extra journal entry. Resolve its integration before confirming.'))
            # _action_done sets today's datetime; explicitly date both the move
            # and its detailed operations with the document accounting date.
            execution_date = (fields.Datetime.now() if product.cost_method == 'fifo'
                              else datetime.combine(self.date, time(12)))
            move.write({'date': execution_date})
            move.move_line_ids.write({'date': execution_date})
            amount = self.currency_id.round(abs(quantity) * cost)
            if self.currency_id.compare_amounts(move.value, amount):
                raise UserError(_('Stock valuation differs from the frozen adjustment value.'))
            if product.cost_method == 'fifo':
                product._recipe_reconcile_fifo(move, fifo_value_before + (-amount if outgoing else amount))
            debit, credit = (difference, valuation) if outgoing else (valuation, difference)
            for account, balance in ((debit, amount), (credit, -amount)):
                account_lines.append(Command.create({
                    'name': '%s - %s' % (self.name, product.display_name),
                    'account_id': account.id, 'product_id': product.id,
                    'debit': max(balance, 0), 'credit': max(-balance, 0),
                    'analytic_distribution': {str(self.analytic_account_id.id): 100} if account == difference and self.analytic_account_id else False,
                }))
            all_moves |= move
            internal(details).write({'move_id': move.id})
        # Zero-quantity documents simply update the recipe; no meaningless zero
        # journal entry or stock move is created.
        if all_moves:
            entry = self.env['account.move'].sudo().create({
                'move_type': 'entry', 'company_id': self.company_id.id,
                'journal_id': journal.id, 'date': self.date, 'ref': self.name,
                'recipe_adjustment_id': self.id,
                'line_ids': account_lines})
            entry.action_post()
            if entry.date != self.date:
                raise UserError(_('The journal date was changed by accounting lock rules. Choose a valid adjustment date.'))
            internal(all_moves).account_move_id = entry
            internal(self).write({'account_move_id': entry.id})
        for line in self.line_ids.filtered('apply_change'):
            line.bom_line_id.product_qty = line.new_quantity
        internal(self).write({'state': 'confirmed'})

    def _open(self, model, ids):
        return {'type': 'ir.actions.act_window', 'name': self.name, 'res_model': model,
                'view_mode': 'list,form', 'domain': [('id', 'in', ids)]}

    def action_sources(self):
        self.ensure_one()
        action = self._open('mrp.bom.consumption.source', self.source_ids.ids)
        action['views'] = [(self.env.ref('mrp_bom_consumption_adjustment.source_list').id, 'list'),
                           (self.env.ref('mrp_bom_consumption_adjustment.source_form').id, 'form')]
        return action

    def action_moves(self):
        self.ensure_one()
        action = self._open('stock.move', self.move_ids.ids)
        action.update(views=[(self.env.ref('mrp_bom_consumption_adjustment.adjustment_stock_move_list').id, 'list'),
                             (self.env.ref('mrp_bom_consumption_adjustment.adjustment_stock_move_form').id, 'form')],
                      search_view_id=[self.env.ref('stock.view_move_search').id, 'search'])
        return action

    def action_account_move(self):
        self.ensure_one()
        if not self.account_move_id:
            raise UserError(_('This adjustment has no journal entry.'))
        return {'type': 'ir.actions.act_window', 'name': self.name,
                'res_model': 'account.move', 'res_id': self.account_move_id.id,
                'view_mode': 'form', 'views': [(self.env.ref('account.view_move_form').id, 'form')],
                'context': {'default_move_type': 'entry'}}


class AdjustmentLine(models.Model):
    _name = 'mrp.bom.consumption.line'
    _description = 'BOM Consumption Component'
    _check_company_auto = True

    adjustment_id = fields.Many2one('mrp.bom.consumption.adjustment', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='adjustment_id.company_id', store=True)
    currency_id = fields.Many2one(related='adjustment_id.currency_id')
    apply_change = fields.Boolean(string='Apply Change')
    bom_line_id = fields.Many2one('mrp.bom.line', required=True, ondelete='restrict')
    product_id = fields.Many2one('product.product', string='Component', required=True)
    uom_id = fields.Many2one('uom.uom', string='UoM', required=True)
    old_quantity = fields.Float(string='Current BOM Quantity', digits='Product Unit', required=True)
    new_quantity = fields.Float(string='New BOM Quantity', digits='Product Unit', required=True)
    detail_ids = fields.One2many('mrp.bom.consumption.detail', 'line_id')
    recorded_quantity = fields.Float(compute='_compute_totals', string='Recorded Consumption')
    required_quantity = fields.Float(compute='_compute_totals', string='Required Consumption')
    previous_quantity = fields.Float(compute='_compute_totals', string='Previous Adjustments')
    adjustment_quantity = fields.Float(compute='_compute_totals', string='Adjustment Quantity')
    unit_cost = fields.Float(compute='_compute_totals', digits=(24, 6))
    adjustment_value = fields.Monetary(compute='_compute_totals')
    exploded = fields.Boolean(compute='_compute_totals', string='Exploded Kit (see details)')

    @api.depends('detail_ids', 'detail_ids.unit_cost', 'detail_ids.adjustment_quantity',
                 'detail_ids.adjustment_value', 'detail_ids.recorded_quantity',
                 'detail_ids.required_quantity', 'detail_ids.previous_quantity')
    def _compute_totals(self):
        for line in self:
            details = line.detail_ids
            line.exploded = any(d.product_id != line.product_id for d in details)
            for field in ('recorded_quantity', 'required_quantity', 'previous_quantity', 'adjustment_quantity'):
                # Adding kg and units across nested leaves is meaningless.
                value = 0 if line.exploded else sum(details.mapped(field))
                if not line.exploded:
                    value = line.product_id.uom_id._compute_quantity(value, line.uom_id, round=False)
                line[field] = value
            line.unit_cost = (details[:1].unit_cost * line.uom_id._compute_quantity(
                1, line.product_id.uom_id, round=False)) if not line.exploded else 0
            line.adjustment_value = sum(details.mapped('adjustment_value'))

    @api.constrains('new_quantity')
    def _check_quantity(self):
        for line in self:
            if line.new_quantity < 0:
                raise ValidationError(_('New BOM quantity cannot be negative.'))

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            bom_line = self.env['mrp.bom.line'].browse(vals.get('bom_line_id'))
            if bom_line:
                vals.update(product_id=bom_line.product_id.id,
                            uom_id=bom_line.product_uom_id.id,
                            old_quantity=bom_line.product_qty)
                vals.setdefault('new_quantity', bom_line.product_qty)
        records = super().create(vals_list)
        if self.env.context.get('recipe_internal') is not _INTERNAL:
            for record in records:
                record.adjustment_id._manager()
                if record.adjustment_id.state != 'draft':
                    raise UserError(_('Components can only be edited in draft.'))
                original = record.bom_line_id
                if (original.bom_id != record.adjustment_id.bom_id or original.product_id != record.product_id
                        or original.product_uom_id != record.uom_id or original.product_qty != record.old_quantity):
                    raise UserError(_('Component data must match the selected BOM line.'))
        return records

    def write(self, vals):
        if self.env.context.get('recipe_internal') is not _INTERNAL:
            for line in self:
                line.adjustment_id._manager()
                if line.adjustment_id.state != 'draft' or set(vals) - {'apply_change', 'new_quantity'}:
                    raise UserError(_('Only the checkbox and new quantity can be edited in draft.'))
        return super().write(vals)

    def unlink(self):
        if self.env.context.get('recipe_internal') is not _INTERNAL and any(l.adjustment_id.state != 'draft' for l in self):
            raise UserError(_('Components can only be removed in draft.'))
        return super().unlink()


class Source(models.Model):
    _name = 'mrp.bom.consumption.source'
    _description = 'BOM Execution Source'
    _rec_name = 'name'
    _check_company_auto = True

    name = fields.Char(compute='_compute_name', string='Execution Document')
    key = fields.Char(required=True, readonly=True, index=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True)
    bom_id = fields.Many2one('mrp.bom', required=True, readonly=True, ondelete='restrict')
    product_id = fields.Many2one('product.product', required=True, readonly=True)
    production_id = fields.Many2one('mrp.production', readonly=True, ondelete='restrict')
    unbuild_id = fields.Many2one('mrp.unbuild', readonly=True, ondelete='restrict')
    picking_id = fields.Many2one('stock.picking', readonly=True, ondelete='restrict')
    execution_date = fields.Datetime(readonly=True)
    move_ids = fields.Many2many('stock.move', 'recipe_source_original_move_rel', readonly=True)
    quantity = fields.Float(string='Executed Quantity (BOM UoM)', digits='Product Unit')
    uom_id = fields.Many2one('uom.uom', required=True, readonly=True)
    basis = fields.Text(readonly=True)
    needs_review = fields.Boolean(readonly=True)
    evidence = fields.Text()
    verified_by = fields.Many2one('res.users', readonly=True)
    verified_on = fields.Datetime(readonly=True)
    _key_company_unique = models.Constraint('unique(key, company_id)', 'Execution source must be unique per company.')

    @api.depends('production_id.name', 'unbuild_id.name', 'picking_id.name', 'move_ids.reference')
    def _compute_name(self):
        for source in self:
            if source.production_id:
                source.name = '%s: %s' % (_('Manufacturing Order'), source.production_id.name)
            elif source.unbuild_id:
                source.name = '%s: %s' % (_('Unbuild Order'), source.unbuild_id.name)
            else:
                source.name = '%s: %s' % (_('Stock Operation'), source.picking_id.name or source.move_ids[:1].reference or source.key)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('recipe_internal') is not _INTERNAL:
            raise AccessError(_('Sources are generated by Review.'))
        return super().create(vals_list)

    def write(self, vals):
        if self.env.context.get('recipe_internal') is not _INTERNAL:
            if not self.env.user.has_group('mrp.group_mrp_manager') or set(vals) - {'quantity', 'evidence'} or any(not s.needs_review for s in self):
                raise AccessError(_('Only unresolved execution quantities and their evidence can be edited.'))
        return super().write(vals)

    def action_verify(self):
        if not self.env.user.has_group('mrp.group_mrp_manager'):
            raise AccessError(_('Only manufacturing administrators can verify execution sources.'))
        for source in self:
            if not source.needs_review or not source.evidence:
                raise UserError(_('Enter evidence for the executed quantity before verification.'))
            internal(source).write({'needs_review': False, 'verified_by': self.env.user.id,
                                    'verified_on': fields.Datetime.now()})

    def action_execution(self):
        self.ensure_one()
        record = self.production_id or self.unbuild_id or self.picking_id
        if not record:
            return {'type': 'ir.actions.act_window', 'res_model': 'stock.move', 'view_mode': 'list,form',
                    'domain': [('id', 'in', self.move_ids.ids)]}
        return {'type': 'ir.actions.act_window', 'res_model': record._name,
                'view_mode': 'form', 'res_id': record.id}


class Detail(models.Model):
    _name = 'mrp.bom.consumption.detail'
    _description = 'BOM Consumption Review Detail'
    _check_company_auto = True

    adjustment_id = fields.Many2one('mrp.bom.consumption.adjustment', required=True, ondelete='cascade')
    line_id = fields.Many2one('mrp.bom.consumption.line', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='adjustment_id.company_id', store=True)
    currency_id = fields.Many2one(related='adjustment_id.currency_id')
    source_id = fields.Many2one('mrp.bom.consumption.source', required=True, ondelete='restrict', index=True)
    execution_date = fields.Datetime(related='source_id.execution_date')
    bom_line_id = fields.Many2one('mrp.bom.line', required=True, ondelete='restrict', index=True)
    leaf_bom_line_id = fields.Many2one('mrp.bom.line', required=True, ondelete='restrict', index=True)
    product_id = fields.Many2one('product.product', required=True)
    uom_id = fields.Many2one('uom.uom', required=True)
    executed_quantity = fields.Float(digits='Product Unit')
    recorded_quantity = fields.Float(digits='Product Unit')
    required_quantity = fields.Float(digits='Product Unit')
    previous_quantity = fields.Float(digits='Product Unit')
    adjustment_quantity = fields.Float(digits='Product Unit')
    unit_cost = fields.Float(digits=(24, 12))
    adjustment_value = fields.Monetary()
    original_move_ids = fields.Many2many('stock.move', 'recipe_detail_original_move_rel')
    move_id = fields.Many2one('stock.move', readonly=True, ondelete='restrict')
    allocation_ids = fields.One2many('mrp.bom.consumption.allocation', 'detail_id')

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('recipe_internal') is not _INTERNAL:
            raise AccessError(_('Review details are generated by the document.'))
        return super().create(vals_list)

    def write(self, vals):
        if self.env.context.get('recipe_internal') is not _INTERNAL:
            if set(vals) - {'allocation_ids'} or any(d.adjustment_id.state not in ('draft', 'reviewed') for d in self):
                raise AccessError(_('Review details are read-only except lot allocations before confirmation.'))
        return super().write(vals)

    def unlink(self):
        if self.env.context.get('recipe_internal') is not _INTERNAL:
            raise AccessError(_('Review details are managed by the document.'))
        return super().unlink()


class StockMoveDocument(models.Model):
    _inherit = 'stock.move'

    recipe_document_id = fields.Many2one(
        related='recipe_adjustment_line_id.adjustment_id', store=True, index=True)


class AccountMove(models.Model):
    _inherit = 'account.move'

    recipe_adjustment_id = fields.Many2one('mrp.bom.consumption.adjustment',
                                          readonly=True, copy=False, index=True, ondelete='restrict')


class Allocation(models.Model):
    _name = 'mrp.bom.consumption.allocation'
    _description = 'Recipe Adjustment Lot Allocation'
    _check_company_auto = True

    detail_id = fields.Many2one('mrp.bom.consumption.detail', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='detail_id.company_id', store=True)
    product_id = fields.Many2one(related='detail_id.product_id')
    lot_id = fields.Many2one('stock.lot', required=True, check_company=True)
    quantity = fields.Float(required=True, digits='Product Unit')

    def _check_editable(self):
        if self.env.context.get('recipe_internal') is not _INTERNAL:
            if not self.env.user.has_group('mrp.group_mrp_manager') or any(
                    a.detail_id.adjustment_id.state not in ('draft', 'reviewed') for a in self):
                raise AccessError(_('Confirmed lot allocations are read-only.'))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._check_editable()
        return records

    def write(self, vals):
        self._check_editable()
        result = super().write(vals)
        self._check_editable()
        return result

    def unlink(self):
        self._check_editable()
        return super().unlink()

