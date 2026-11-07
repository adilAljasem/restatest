from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

ISSUE_TYPES = ('new', 'replace')
INCIDENT_TYPES = ('damage', 'loss')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    ess_is_custody = fields.Boolean(string='Employee custody item',
                                    help='Employees can request this item from the self-service portal.')
    ess_renew_months = fields.Integer(string='Renew every (months)',
                                      help='Periodic entitlement, e.g. 6 for two uniforms a year. 0 = no renewal.')
    ess_needs_size = fields.Boolean(string='Requires a size')


class BsHrCustodyRequest(models.Model):
    _name = 'bs.hr.custody.request'
    _description = 'Custody Request'
    _inherit = ['bs.hr.request.mixin']
    _ess_sequence_code = 'bs.hr.custody.request'

    request_type = fields.Selection([
        ('new', 'New item'),
        ('replace', 'Replacement'),
        ('return', 'Return'),
        ('damage', 'Damage report'),
        ('loss', 'Loss report'),
    ], required=True, default='new', tracking=True)
    line_ids = fields.One2many('bs.hr.custody.request.line', 'request_id', string='Items', copy=True)
    reason = fields.Text()
    picking_id = fields.Many2one('stock.picking', string='Transfer', readonly=True, copy=False)
    currency_id = fields.Many2one(related='company_id.currency_id')
    deduction_amount = fields.Monetary(
        string='Deduction from salary', tracking=True,
        help='Amount deducted from the payroll for a damaged or lost item (set by the approver).')
    adjustment_id = fields.Many2one('bs.hr.payroll.adjustment', readonly=True, copy=False)
    estimated_value = fields.Monetary(compute='_compute_estimated_value')

    @api.depends('line_ids.holding_id.value', 'line_ids.product_id', 'line_ids.quantity')
    def _compute_estimated_value(self):
        for rec in self:
            total = 0.0
            for line in rec.line_ids:
                if line.holding_id:
                    total += line.holding_id.value
                else:
                    total += line.product_id.standard_price * line.quantity
            rec.estimated_value = total

    # ------------------------------------------------------------------
    # request hooks
    # ------------------------------------------------------------------
    def _ess_check_submit(self):
        for rec in self:
            if not rec.line_ids:
                raise ValidationError(self.env._('Add at least one item.'))
            for line in rec.line_ids:
                if rec.request_type in ('return',) + INCIDENT_TYPES or (rec.request_type == 'replace' and line.holding_id):
                    if not line.holding_id:
                        raise ValidationError(self.env._('Select the item you hold for each line.'))
                    if line.holding_id.employee_id != rec.employee_id or line.holding_id.state != 'held':
                        raise ValidationError(self.env._('The item %s is not in your custody.',
                                                         line.holding_id.display_name))
                if rec.request_type in ISSUE_TYPES:
                    if not line.product_id.ess_is_custody:
                        raise ValidationError(self.env._('%s is not a custody item.', line.product_id.display_name))
                    if line.product_id.ess_needs_size and not line.size:
                        raise ValidationError(self.env._('Please give the size for %s.', line.product_id.display_name))
            if rec.request_type in INCIDENT_TYPES and not (rec.reason or '').strip():
                raise ValidationError(self.env._('Please describe what happened.'))

    def _ess_on_approved(self):
        for rec in self:
            if rec.request_type in ISSUE_TYPES:
                rec._ess_create_picking(outgoing=True)
            elif rec.request_type == 'return':
                rec._ess_create_picking(outgoing=False)
            else:
                rec._ess_process_incident()

    def _ess_on_cancelled(self):
        for rec in self:
            picking = rec.picking_id.sudo()
            if picking and picking.state not in ('done', 'cancel'):
                picking.action_cancel()
            if rec.adjustment_id and rec.adjustment_id.state == 'pending':
                rec.adjustment_id.sudo().action_cancel()

    def _ess_portal_approval_fields(self):
        if self and self[:1].request_type in INCIDENT_TYPES:
            return ['deduction_amount']
        return []

    def _ess_portal_summary(self):
        self.ensure_one()
        type_label = dict(self._fields['request_type']._description_selection(self.env)).get(self.request_type)
        items = ', '.join('%s x%g' % (l.product_id.display_name, l.quantity) for l in self.line_ids)
        return '%s · %s' % (type_label, items)

    def _ess_portal_detail_fields(self):
        return ['request_type', 'line_ids', 'reason', 'estimated_value', 'deduction_amount']

    def _ess_portal_url(self):
        self.ensure_one()
        return '/my/hr/request/%s/%s' % (self._name, self.id)

    # ------------------------------------------------------------------
    # stock
    # ------------------------------------------------------------------
    def _ess_stock_locations(self):
        self.ensure_one()
        company = self.company_id or self.env.company
        warehouse = self.env['stock.warehouse'].sudo().search([('company_id', '=', company.id)], limit=1)
        if not warehouse:
            raise UserError(self.env._('No warehouse is configured for %s.', company.name))
        return warehouse, warehouse.lot_stock_id, company._ess_custody_location()

    def _ess_create_picking(self, outgoing=True):
        self.ensure_one()
        warehouse, stock_location, custody_location = self._ess_stock_locations()
        source, dest = (stock_location, custody_location) if outgoing else (custody_location, stock_location)
        picking_type = warehouse.int_type_id.sudo()
        if not picking_type:
            raise UserError(self.env._('The warehouse %s has no internal transfer type.', warehouse.name))
        partner = self.employee_id.sudo().work_contact_id
        moves = []
        for line in self.line_ids:
            product = line.holding_id.product_id if (not outgoing and line.holding_id) else line.product_id
            moves.append((0, 0, {
                'product_id': product.id,
                'product_uom_qty': line.quantity,
                'product_uom': product.uom_id.id,
                'location_id': source.id,
                'location_dest_id': dest.id,
                'company_id': warehouse.company_id.id,
            }))
        picking = self.env['stock.picking'].sudo().create({
            'picking_type_id': picking_type.id,
            'location_id': source.id,
            'location_dest_id': dest.id,
            'partner_id': partner.id,
            'origin': self.name,
            'company_id': warehouse.company_id.id,
            'ess_custody_request_id': self.id,
            'move_ids': moves,
        })
        picking.action_confirm()
        picking.action_assign()
        self.picking_id = picking
        return picking

    def _ess_on_picking_done(self, picking):
        """Called when the custody transfer is validated."""
        Holding = self.env['bs.hr.custody.holding'].sudo()
        for rec in self:
            today = fields.Date.context_today(rec)
            if rec.request_type in ISSUE_TYPES:
                for line in rec.line_ids:
                    moves = picking.move_ids.filtered(lambda m: m.product_id == line.product_id and m.state == 'done')
                    qty = sum(moves.mapped('quantity')) or line.quantity
                    lot = moves.move_line_ids.lot_id[:1]
                    if line.holding_id and line.holding_id.state == 'held':
                        line.holding_id.sudo().write({'state': 'returned', 'date_returned': today})
                    Holding.create({
                        'employee_id': rec.employee_id.id,
                        'product_id': line.product_id.id,
                        'quantity': min(qty, line.quantity) if qty else line.quantity,
                        'size': line.size,
                        'lot_id': lot.id,
                        'date_received': today,
                        'request_id': rec.id,
                        'value': line.product_id.standard_price * line.quantity,
                    })
            elif rec.request_type == 'return':
                rec.line_ids.holding_id.sudo().write({'state': 'returned', 'date_returned': today})
            if rec.state == 'approved':
                rec.state = 'done'
            rec._ess_notify_employee(
                rec._ess_lang_env()._('Custody updated'),
                rec._ess_lang_env()._('The transfer for your request %s is done.', rec.name))

    def _ess_process_incident(self):
        self.ensure_one()
        new_state = 'damaged' if self.request_type == 'damage' else 'lost'
        self.line_ids.holding_id.sudo().write({'state': new_state,
                                               'date_returned': fields.Date.context_today(self)})
        if self.deduction_amount > 0:
            self.adjustment_id = self.env['bs.hr.payroll.adjustment']._ess_create_from(
                self, self.employee_id, self.deduction_amount,
                self.env._('Custody %(state)s - %(ref)s', state=dict(
                    self._fields['request_type']._description_selection(self.env))[self.request_type],
                    ref=self.name),
                'custody')
        self.state = 'done'

    def action_view_picking(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'stock.picking', 'res_id': self.picking_id.id,
                'view_mode': 'form'}


class BsHrCustodyRequestLine(models.Model):
    _name = 'bs.hr.custody.request.line'
    _description = 'Custody Request Line'

    request_id = fields.Many2one('bs.hr.custody.request', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one(related='request_id.employee_id')
    product_id = fields.Many2one('product.product', string='Item', required=True,
                                 domain="[('ess_is_custody', '=', True)]")
    quantity = fields.Float(default=1.0, required=True)
    size = fields.Char()
    holding_id = fields.Many2one('bs.hr.custody.holding', string='Held item',
                                 domain="[('employee_id', '=', employee_id), ('state', '=', 'held')]")

    _quantity_positive = models.Constraint('CHECK(quantity > 0)', 'The quantity must be positive.')

    @api.onchange('holding_id')
    def _onchange_holding_id(self):
        for line in self:
            if line.holding_id:
                line.product_id = line.holding_id.product_id
                line.quantity = line.holding_id.quantity
                line.size = line.holding_id.size

    def _compute_display_name(self):
        for line in self:
            name = '%s x%g' % (line.product_id.display_name or '', line.quantity)
            if line.size:
                name += ' (%s)' % line.size
            line.display_name = name


class BsHrCustodyHolding(models.Model):
    _name = 'bs.hr.custody.holding'
    _description = 'Employee Custody Holding'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_received desc, id desc'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, tracking=True)
    company_id = fields.Many2one(related='employee_id.company_id', store=True, index=True)
    work_location_id = fields.Many2one(related='employee_id.work_location_id', store=True, string='Branch')
    product_id = fields.Many2one('product.product', string='Item', required=True)
    quantity = fields.Float(default=1.0)
    size = fields.Char()
    lot_id = fields.Many2one('stock.lot', string='Serial / Lot')
    date_received = fields.Date(default=fields.Date.context_today, required=True)
    date_returned = fields.Date()
    ack_date = fields.Datetime(string='Acknowledged on', readonly=True, copy=False,
                               help='Electronic acknowledgement of receipt by the employee.')
    state = fields.Selection([
        ('held', 'Held'),
        ('returned', 'Returned'),
        ('lost', 'Lost'),
        ('damaged', 'Damaged'),
    ], default='held', required=True, index=True, tracking=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    value = fields.Monetary()
    next_due_date = fields.Date(compute='_compute_next_due_date', store=True, string='Renewal due')
    request_id = fields.Many2one('bs.hr.custody.request', readonly=True, string='Request')
    renewal_notified = fields.Boolean(copy=False)

    @api.depends('date_received', 'product_id.ess_renew_months', 'state')
    def _compute_next_due_date(self):
        for holding in self:
            months = holding.product_id.product_tmpl_id.ess_renew_months
            if holding.state == 'held' and months and holding.date_received:
                holding.next_due_date = holding.date_received + relativedelta(months=months)
            else:
                holding.next_due_date = False

    def _compute_display_name(self):
        for holding in self:
            name = holding.product_id.display_name or ''
            if holding.size:
                name += ' (%s)' % holding.size
            if holding.lot_id:
                name += ' [%s]' % holding.lot_id.name
            holding.display_name = name

    def action_acknowledge(self):
        self.filtered(lambda h: not h.ack_date).write({'ack_date': fields.Datetime.now()})
        return True

    @api.model
    def _cron_renewal_reminder(self, days=7):
        today = fields.Date.context_today(self)
        holdings = self.sudo().search([
            ('state', '=', 'held'), ('next_due_date', '!=', False),
            ('next_due_date', '<=', today + relativedelta(days=days)), ('renewal_notified', '=', False)])
        hr_users = self.env.ref('bs_hr_ess_base.group_ess_hr_officer').sudo().all_user_ids.filtered(
            lambda u: not u.share)
        for holding in holdings:
            employee = holding.employee_id.sudo()
            title = self.env._('Custody renewal due')
            body = self.env._('%(item)s is due for renewal on %(date)s.', item=holding.display_name,
                              date=holding.next_due_date)
            self.env['bs.hr.notification'].sudo().create({
                'employee_id': employee.id, 'title': title, 'body': body, 'url': '/my/hr/custody',
                'res_model': holding._name, 'res_id': holding.id,
            })
            for user in hr_users.filtered(lambda u: employee.company_id in u.company_ids)[:5]:
                holding.activity_schedule('mail.mail_activity_data_todo', user_id=user.id,
                                          summary='%s - %s' % (employee.name, title),
                                          date_deadline=holding.next_due_date)
            holding.renewal_notified = True


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    ess_custody_request_id = fields.Many2one('bs.hr.custody.request', string='Custody request',
                                             readonly=True, copy=False, index='btree_not_null')

    def _action_done(self):
        res = super()._action_done()
        for picking in self.filtered(lambda p: p.ess_custody_request_id and p.state == 'done'):
            picking.ess_custody_request_id.sudo()._ess_on_picking_done(picking)
        return res
