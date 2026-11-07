# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

CC_STATUS = [
    ('dispatched', 'Sent to Branch'),
    ('kitchen', 'In Kitchen'),
    ('paid', 'Paid'),
    ('cancel', 'Cancelled'),
]


class PosOrder(models.Model):
    _inherit = 'pos.order'

    cc_call_center_id = fields.Many2one(
        'pos.call.center', string='Call Center', readonly=True, copy=False, index='btree_not_null')
    cc_call_center_name = fields.Char(related='cc_call_center_id.name', string='Call Center Name')
    cc_ref = fields.Char('Call Center Ref.', readonly=True, copy=False, index='btree_not_null')
    cc_agent_id = fields.Many2one('res.users', string='Call Center Agent', readonly=True, copy=False)
    cc_origin_config_id = fields.Many2one('pos.config', string='Taken on', readonly=True, copy=False)
    cc_zone_id = fields.Many2one('pos.call.zone', string='Zone', readonly=True, copy=False)
    cc_dispatch_date = fields.Datetime('Sent to Branch on', readonly=True, copy=False)
    cc_kitchen_state = fields.Selection([
        ('none', 'Not sent'),
        ('pending', 'Waiting for the branch'),
        ('sent', 'Sent to kitchen'),
    ], string='Kitchen', readonly=True, copy=False)
    cc_kitchen_date = fields.Datetime('Sent to Kitchen on', readonly=True, copy=False)
    cc_transfer_count = fields.Integer('Transfers', readonly=True, copy=False)
    cc_status = fields.Selection(
        CC_STATUS, string='Call Center Status', compute='_compute_cc_status', store=True, index=True)

    @api.depends('state', 'cc_call_center_id', 'cc_kitchen_state')
    def _compute_cc_status(self):
        for order in self:
            if not order.cc_call_center_id:
                order.cc_status = False
            elif order.state == 'cancel':
                order.cc_status = 'cancel'
            elif order.state in ('paid', 'done', 'invoiced'):
                order.cc_status = 'paid'
            elif order.cc_kitchen_state == 'sent':
                order.cc_status = 'kitchen'
            else:
                order.cc_status = 'dispatched'

    # ------------------------------------------------------------------
    # Protection of the call center data against UI synchronisation
    # ------------------------------------------------------------------
    @api.model
    def _process_order(self, order, existing_order):
        # cc_* values are managed by the server only
        for key in [k for k in order if isinstance(k, str) and k.startswith('cc_')]:
            order.pop(key)
        if existing_order and existing_order.cc_call_center_id:
            session = self.env['pos.session'].browse(order.get('session_id') or 0).exists()
            if session and session.config_id != existing_order.config_id and session.config_id.cc_is_terminal:
                # A call center device still had a copy of an order already sent to a branch:
                # never move it back.
                _logger.info('Call center: ignored stale sync of %s from %s',
                             existing_order.cc_ref, session.config_id.name)
                return existing_order.id
        return super()._process_order(order, existing_order)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @api.model
    def _cc_check_branch_open(self, center, target):
        if not target or target not in center._get_scope_configs():
            raise UserError(_('Branch "%s" is not in the scope of this call center.',
                              target.name if target else '?'))
        session = target.current_session_id
        if not session or session.state != 'opened':
            raise UserError(_('Branch "%s" has no open session right now. '
                              'Choose a branch that is currently working.', target.name))
        return session

    def _cc_move_to_branch(self, center, target):
        """Move this draft order into the open session of the target branch, renumber it
        for that branch and reset its preparation state so the branch kitchen receives
        every line."""
        self.ensure_one()
        session = self._cc_check_branch_open(center, target)
        vals = {'session_id': session.id}
        if self.preset_id and target.use_presets:
            allowed = target.available_preset_ids | target.default_preset_id
            if self.preset_id not in allowed:
                vals['preset_id'] = target.default_preset_id.id
        reference, tracking_number = target._get_next_order_refs()
        vals.update(pos_reference=reference, tracking_number=tracking_number)
        self._update_sequence_number(session, vals)
        self.write(vals)
        self.flush_recordset()
        # bypass the "keep last preparation change" protection of pos.order.write()
        self.env.cr.execute(
            "UPDATE pos_order SET last_order_preparation_change = NULL WHERE id = %s", [self.id])
        self.invalidate_recordset(['last_order_preparation_change'])
        return session

    def _cc_notify(self, config, event):
        config = config.sudo()
        try:
            config._notify(event, {'order_id': self.id, 'uuid': self.uuid, 'cc_ref': self.cc_ref})
        except Exception:  # never block the business flow because of the bus
            _logger.exception('Call center: bus notification failed for %s', self.cc_ref)

    @api.model
    def _cc_get_branch_order(self, order_id):
        """Order of the branch of the current user (POS user of an opened session)."""
        order = self.browse(int(order_id)).exists()
        if not order or not order.cc_call_center_id:
            raise UserError(_('Unknown call center order.'))
        order.check_access('read')
        return order.sudo()

    # ------------------------------------------------------------------
    # Call center POS (agent)
    # ------------------------------------------------------------------
    @api.model
    def cc_dispatch(self, order_id, target_config_id, zone_id=False):
        order = self.browse(int(order_id)).exists()
        if not order:
            raise UserError(_('The order was not found, please try again.'))
        if order.cc_call_center_id:
            raise UserError(_('This order was already sent to %s.', order.config_id.name))
        terminal = order.config_id
        center = terminal.sudo().cc_terminal_center_id
        if not center:
            raise UserError(_('This order was not taken on a call center POS.'))
        center._check_agent()
        if order.state != 'draft':
            raise UserError(_('Only unpaid orders can be sent.'))
        if not order.lines:
            raise UserError(_('The order is empty.'))
        if center.require_customer and not order.partner_id:
            raise UserError(_('Please select the customer before sending the order.'))

        order = order.sudo()
        target = self.env['pos.config'].sudo().browse(int(target_config_id)).exists()
        zone = self.env['pos.call.zone'].sudo().browse(int(zone_id or 0)).exists()
        cc_ref = self.env['ir.sequence'].sudo().next_by_code('pos.call.order') or '/'
        order._cc_move_to_branch(center, target)
        order.write({
            'cc_call_center_id': center.id,
            'cc_ref': cc_ref,
            'cc_agent_id': self.env.uid,
            'cc_origin_config_id': terminal.id,
            'cc_zone_id': zone.id,
            'cc_dispatch_date': fields.Datetime.now(),
            'cc_kitchen_state': 'pending' if center.auto_send_kitchen else 'none',
            'floating_order_name': order.floating_order_name or ' - '.join(
                filter(None, [cc_ref, order.partner_id.name])),
        })
        order.message_post(body=_('Call center order %(ref)s taken by %(agent)s, sent to branch %(branch)s.',
                                  ref=cc_ref, agent=self.env.user.name, branch=target.name))
        order._cc_notify(target, 'CC_ORDER')
        order._cc_notify(terminal, 'CC_ORDER_REMOVED')
        return {
            'cc_ref': cc_ref,
            'branch': target.name,
            'tracking_number': order.tracking_number,
        }

    # ------------------------------------------------------------------
    # Branch POS
    # ------------------------------------------------------------------
    @api.model
    def cc_branch_pending(self, config_id):
        """Waiting call center orders of a branch (used at start-up and as polling fallback)."""
        config = self.env['pos.config'].browse(int(config_id)).exists()
        if not config:
            return []
        config.check_access('read')
        orders = self.sudo().search([
            ('config_id', '=', config.id), ('state', '=', 'draft'),
            ('cc_call_center_id', '!=', False),
        ], order='cc_dispatch_date, id')
        return [{'id': o.id, 'uuid': o.uuid, 'kitchen_state': o.cc_kitchen_state} for o in orders]

    @api.model
    def cc_claim_kitchen(self, order_id):
        """Only one device of the branch may send the order to the kitchen."""
        order = self._cc_get_branch_order(order_id)
        self.env.cr.execute(
            "SELECT cc_kitchen_state, state FROM pos_order WHERE id = %s FOR UPDATE", [order.id])
        kitchen_state, state = self.env.cr.fetchone()
        if kitchen_state != 'pending' or state != 'draft':
            return False
        order.write({'cc_kitchen_state': 'sent', 'cc_kitchen_date': fields.Datetime.now()})
        order.flush_recordset()
        return True

    @api.model
    def cc_release_kitchen(self, order_id):
        order = self._cc_get_branch_order(order_id)
        if order.cc_kitchen_state == 'sent' and order.state == 'draft':
            order.write({'cc_kitchen_state': 'pending', 'cc_kitchen_date': False})
        return True

    @api.model
    def cc_get_transfer_targets(self, order_id):
        order = self._cc_get_branch_order(order_id)
        center = order.cc_call_center_id
        if not center.allow_branch_transfer or order.state != 'draft':
            return []
        return [{'id': c.id, 'name': c.name} for c in center._get_open_configs() - order.config_id]

    @api.model
    def cc_transfer(self, order_id, target_config_id):
        order = self._cc_get_branch_order(order_id)
        center = order.cc_call_center_id
        if not center.allow_branch_transfer:
            raise UserError(_('Transfers between branches are disabled for this call center.'))
        if order.state != 'draft':
            raise UserError(_('Only unpaid orders can be transferred.'))
        source = order.config_id
        target = self.env['pos.config'].sudo().browse(int(target_config_id)).exists()
        if target == source:
            raise UserError(_('The order is already in this branch.'))
        order._cc_move_to_branch(center, target)
        order.write({
            'cc_transfer_count': order.cc_transfer_count + 1,
            'cc_kitchen_state': 'pending' if center.auto_send_kitchen else 'none',
            'cc_kitchen_date': False,
        })
        order.message_post(body=_('Transferred from %(src)s to %(dst)s by %(user)s.',
                                  src=source.name, dst=target.name, user=self.env.user.name))
        order._cc_notify(source, 'CC_ORDER_REMOVED')
        order._cc_notify(target, 'CC_ORDER')
        return {'branch': target.name}
