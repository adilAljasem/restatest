# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PosCallCenter(models.Model):
    _name = 'pos.call.center'
    _description = 'POS Call Center'
    _inherit = ['mail.thread']
    _order = 'sequence, name'

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    user_ids = fields.Many2many(
        'res.users', 'pos_call_center_user_rel', 'center_id', 'user_id',
        string='Agents', help='Users working in this call center.')

    # ---------------- Terminal (where agents take the orders) ----------------
    terminal_config_ids = fields.One2many(
        'pos.config', 'cc_terminal_center_id', string='Call Center POS',
        help='Point(s) of Sale used by the agents to take the orders. '
             'On these POS the "Payment" button is replaced by "Send to Branch".')

    # ---------------- Scope ----------------
    scope_type = fields.Selection([
        ('all', 'All Points of Sale of the company'),
        ('selected', 'Selected Points of Sale only'),
    ], string='Scope', default='selected', required=True, tracking=True)
    config_ids = fields.Many2many(
        'pos.config', 'pos_call_center_config_rel', 'center_id', 'config_id',
        string='Branches in Scope',
        domain="[('company_id', '=', company_id), ('cc_terminal_center_id', '=', False)]")
    zone_ids = fields.One2many('pos.call.zone', 'call_center_id', string='Zones')

    # ---------------- Behaviour ----------------
    auto_send_kitchen = fields.Boolean(
        'Send to kitchen automatically', default=True,
        help='When the order reaches the branch it is sent immediately to the kitchen '
             'printers / preparation display, exactly like the "Order" button.')
    require_customer = fields.Boolean(
        'Customer required', default=True,
        help='The agent must select a customer before sending the order.')
    allow_branch_transfer = fields.Boolean(
        'Branches can transfer', default=True,
        help='A branch can forward a waiting order to another open branch of the same scope.')

    # ---------------- Computed ----------------
    open_config_ids = fields.Many2many(
        'pos.config', compute='_compute_open_configs', string='Open Branches Now')
    open_config_count = fields.Integer(compute='_compute_open_configs')
    order_count = fields.Integer(compute='_compute_order_count')

    @api.constrains('terminal_config_ids', 'config_ids')
    def _check_terminal_not_branch(self):
        for center in self:
            both = center.terminal_config_ids & center.config_ids
            if both:
                raise ValidationError(_(
                    'The POS "%s" cannot be at the same time a call center POS and a branch.',
                    both[0].name))

    # ------------------------------------------------------------------
    def _get_scope_configs(self):
        self.ensure_one()
        if self.scope_type == 'all':
            configs = self.env['pos.config'].sudo().search([('company_id', '=', self.company_id.id)])
        else:
            configs = self.config_ids.sudo()
        return configs.filtered(lambda c: not c.cc_terminal_center_id)

    def _get_open_configs(self):
        """Branches of the scope that currently have an opened session."""
        self.ensure_one()
        return self._get_scope_configs().filtered(
            lambda c: c.current_session_id and c.current_session_state == 'opened')

    @api.depends('scope_type', 'config_ids', 'company_id')
    def _compute_open_configs(self):
        for center in self:
            opened = center._get_open_configs()
            center.open_config_ids = opened
            center.open_config_count = len(opened)

    def _compute_order_count(self):
        data = self.env['pos.order']._read_group(
            [('cc_call_center_id', 'in', self.ids)], ['cc_call_center_id'], ['__count'])
        counts = {center.id: count for center, count in data}
        for center in self:
            center.order_count = counts.get(center.id, 0)

    @api.model
    def _get_centers_for_branch(self, config):
        centers = self.sudo().search([('company_id', '=', config.company_id.id)])
        return centers.filtered(lambda c: config in c._get_scope_configs())

    def _check_agent(self):
        """The current user must be an agent of the call center (or a manager)."""
        self.ensure_one()
        user = self.env.user
        if user in self.sudo().user_ids or user.has_group('pos_call_center.group_call_center_manager'):
            return True
        raise UserError(_('You are not an agent of call center "%s".', self.name))

    # ------------------------------------------------------------------
    # Called from the call center POS
    # ------------------------------------------------------------------
    @api.model
    def cc_get_targets(self, terminal_config_id):
        config = self.env['pos.config'].browse(int(terminal_config_id)).exists()
        center = config.sudo().cc_terminal_center_id
        if not center:
            return {'branches': [], 'zones': []}
        center._check_agent()
        opened = center._get_open_configs()
        return {
            'center': center.name,
            'require_customer': center.require_customer,
            'branches': [{'id': c.id, 'name': c.name} for c in opened],
            'zones': [{
                'id': z.id,
                'name': z.name,
                'config_ids': (z.config_ids & opened).ids,
            } for z in center.zone_ids],
        }

    # ------------------------------------------------------------------
    # Backend actions
    # ------------------------------------------------------------------
    def action_view_orders(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('pos_call_center.action_cc_pos_orders')
        action['domain'] = [('cc_call_center_id', '=', self.id)]
        action['context'] = {}
        return action

    def action_open_terminal(self):
        self.ensure_one()
        terminal = self.terminal_config_ids[:1]
        if not terminal:
            raise UserError(_('Please set the Call Center POS of "%s" first.', self.name))
        return terminal.open_ui()

    def action_create_terminal(self):
        """Create the POS used by the agents, with the same products / presets / pricing as the
        branches, but without kitchen printers or preparation display."""
        self.ensure_one()
        if self.terminal_config_ids:
            return self.action_open_terminal()
        ref = self._get_scope_configs()[:1]
        vals = {
            'name': _('%s - Call Center POS', self.name),
            'company_id': self.company_id.id,
            'cc_terminal_center_id': self.id,
            'module_pos_restaurant': False,
            'is_order_printer': False,
        }
        if ref:
            vals.update({
                'use_presets': ref.use_presets,
                'default_preset_id': ref.default_preset_id.id,
                'available_preset_ids': [(6, 0, ref.available_preset_ids.ids)],
                'use_pricelist': ref.use_pricelist,
                'pricelist_id': ref.pricelist_id.id,
                'available_pricelist_ids': [(6, 0, ref.available_pricelist_ids.ids)],
                'default_fiscal_position_id': ref.default_fiscal_position_id.id,
                'fiscal_position_ids': [(6, 0, ref.fiscal_position_ids.ids)],
                'iface_tax_included': ref.iface_tax_included,
                'limit_categories': ref.limit_categories,
                'iface_available_categ_ids': [(6, 0, ref.iface_available_categ_ids.ids)],
            })
        config = self.env['pos.config'].with_company(self.company_id).create(vals)
        if not config.payment_method_ids and ref:
            # a POS needs one payment method to open a session (never used here)
            config.payment_method_ids = ref.payment_method_ids.filtered(lambda m: not m.is_cash_count)[:1]
        self.message_post(body=_('Call center POS "%s" created.', config.name))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _('Call center POS "%s" created. Agents can now use the menu "Take Orders".', config.name),
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    @api.model
    def action_open_my_terminal(self):
        Center = self.search([])
        mine = Center.filtered(lambda c: self.env.user in c.user_ids) or Center
        center = mine.filtered('terminal_config_ids')[:1]
        if center:
            return center.action_open_terminal()
        if self.env.user.has_group('pos_call_center.group_call_center_manager'):
            # guide the manager to the configuration instead of an error
            action = self.env['ir.actions.act_window']._for_xml_id('pos_call_center.action_pos_call_center')
            if len(mine) == 1:
                action.update({'res_id': mine.id, 'views': [(False, 'form')], 'view_mode': 'form'})
            return action
        raise UserError(_('No call center POS is configured for you. '
                          'Ask your manager to set it in Call Center > Configuration.'))


class PosCallZone(models.Model):
    _name = 'pos.call.zone'
    _description = 'Call Center Delivery Zone'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    call_center_id = fields.Many2one('pos.call.center', required=True, ondelete='cascade')
    config_ids = fields.Many2many(
        'pos.config', 'pos_call_zone_config_rel', 'zone_id', 'config_id',
        string='Serving Branches',
        help='When this zone is selected in the call center POS, only these branches '
             '(if open) are proposed.')
    note = fields.Char()
