import json
import secrets
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

TRACKING_STATES = [
    ('received', 'Received'),
    ('preparing', 'Preparing'),
    ('ready', 'Ready'),
    ('completed', 'Picked up'),
    ('cancelled', 'Cancelled'),
]

# Fields managed by the server only: never accept them from the POS frontend sync,
# otherwise a stale client copy could overwrite the status set by the kitchen screen.
SERVER_MANAGED_FIELDS = (
    'tracking_state', 'tracking_ready_date', 'tracking_completed_date',
    'tracking_nps_reminder_sent', 'nps_score', 'tracking_ready_ack', 'tracking_ready_push_count',
    'tracking_ready_last_push',
)
READY_REPEAT_SECONDS = 60


def _new_tracking_token():
    return secrets.token_urlsafe(16)


class PosOrder(models.Model):
    _inherit = 'pos.order'

    tracking_token = fields.Char(copy=False, readonly=True, index='btree_not_null')
    tracking_state = fields.Selection(
        TRACKING_STATES, string="Customer Status", default='received', copy=False, readonly=True,
    )
    tracking_ready_date = fields.Datetime(string="Ready At", copy=False, readonly=True)
    tracking_completed_date = fields.Datetime(string="Picked Up At", copy=False, readonly=True)
    tracking_nps_reminder_sent = fields.Boolean(copy=False, readonly=True)
    tracking_ready_ack = fields.Boolean(
        string="Customer Saw Ready Alert", copy=False, readonly=True,
        help="The customer opened the page (or stopped the alert) after the order was ready.")
    tracking_ready_push_count = fields.Integer(copy=False, readonly=True)
    tracking_ready_last_push = fields.Datetime(copy=False, readonly=True)
    nps_score = fields.Integer(string="NPS Score", default=-1, copy=False, readonly=True,
                               help="Customer answer (0-10), -1 when not answered.")
    tracking_url = fields.Char(string="Tracking Link", compute='_compute_tracking_url')

    _tracking_token_unique = models.Constraint(
        'unique(tracking_token)', 'The tracking token must be unique.',
    )

    @api.depends('tracking_token')
    def _compute_tracking_url(self):
        for order in self:
            order.tracking_url = order.tracking_token and f"{order.get_base_url()}/track/{order.tracking_token}"

    # ------------------------------------------------------------------
    # Sync from the POS frontend
    # ------------------------------------------------------------------
    @api.model
    def _process_order(self, order, existing_order):
        for key in SERVER_MANAGED_FIELDS:
            order.pop(key, None)
        if existing_order and existing_order.tracking_token:
            order.pop('tracking_token', None)
        return super()._process_order(order, existing_order)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('tracking_token'):
                vals['tracking_token'] = _new_tracking_token()
        return super().create(vals_list)

    def _tracking_ensure_token(self):
        for order in self.filtered(lambda o: not o.tracking_token):
            order.tracking_token = _new_tracking_token()

    def _tracking_is_eligible(self):
        """Tracking applies to this order (POS enabled + order type + cashier filters)."""
        self.ensure_one()
        return bool(self.config_id) and self.config_id._tracking_order_eligible(self)

    # ------------------------------------------------------------------
    # Status computation (driven by the preparation display stages)
    # ------------------------------------------------------------------
    def _tracking_compute_state(self):
        """Return the customer-facing status computed from the preparation display data.

        The order always starts as "preparing" while its items sit on the first stage of the
        screen, whatever the number of stages: it only becomes "ready" from the ready stage and
        "completed" (picked up) from a later, last stage. See pos.config._tracking_stage_levels.
        """
        self.ensure_one()
        if self.state == 'cancel':
            return 'cancelled'
        displays = self.config_id._tracking_prep_displays()
        prep_orders = self.env['pos.prep.order'].sudo().search([('pos_order_id', '=', self.id)])
        if not displays or not prep_orders:
            return 'received'

        states = self.env['pos.prep.state'].sudo().search([
            ('prep_line_id.prep_order_id', 'in', prep_orders.ids),
            ('stage_id', 'in', displays.stage_ids.ids),
        ])
        # Ignore fully cancelled lines and combo parents (their children carry the work).
        states = states.filtered(
            lambda s: (s.prep_line_id.quantity - s.prep_line_id.cancelled) > 0
        )
        if not states:
            return 'received'

        all_ready = True
        all_done = True
        for display in displays:
            stage_ids, ready_index, done_index = self.config_id._tracking_stage_levels(display)
            display_states = states.filtered(lambda s: s.stage_id.id in stage_ids)
            for st in display_states:
                index = stage_ids.index(st.stage_id.id)
                if ready_index is None or index < ready_index:
                    all_ready = False
                if done_index is None or index < done_index:
                    all_done = False
        if all_done:
            return 'completed'
        if all_ready:
            return 'ready'
        return 'preparing'

    def _tracking_sync(self):
        """Recompute the status and react to transitions (notifications, dates)."""
        now = fields.Datetime.now()
        to_notify = self.env['pos.order']
        for order in self.sudo():
            if not order._tracking_is_eligible() or not order.config_id.tracking_pager_enabled:
                continue
            new_state = order._tracking_compute_state()
            old_state = order.tracking_state or 'received'
            if new_state == old_state:
                continue
            vals = {'tracking_state': new_state}
            if new_state in ('ready', 'completed') and not order.tracking_ready_date:
                vals['tracking_ready_date'] = now
            if new_state == 'completed':
                vals['tracking_completed_date'] = now
            if new_state in ('received', 'preparing'):
                # New items were sent to the kitchen: the order is no longer ready.
                vals.update(tracking_ready_date=False, tracking_completed_date=False,
                            tracking_nps_reminder_sent=False, tracking_ready_ack=False,
                            tracking_ready_push_count=0, tracking_ready_last_push=False)
            order.write(vals)
            if new_state in ('ready', 'completed') and old_state in ('received', 'preparing'):
                to_notify |= order
        if to_notify:
            to_notify._tracking_notify_ready()
        return True

    def _tracking_notify_ready(self):
        """First 'ready' alert; repeated by _cron_tracking_ready_repeat until the customer reacts."""
        self = self.sudo()
        self.write({
            'tracking_ready_ack': False,
            'tracking_ready_push_count': 1,
            'tracking_ready_last_push': fields.Datetime.now(),
        })
        self._tracking_queue_push('ready')
        if any(o.config_id.tracking_alert_repeat > 1 for o in self):
            self.env.ref('pos_order_tracking_nps.ir_cron_tracking_ready_repeat').sudo()._trigger(
                at=fields.Datetime.now() + timedelta(seconds=READY_REPEAT_SECONDS))

    def _tracking_ack_ready(self):
        self.sudo().filtered(lambda o: not o.tracking_ready_ack).write({'tracking_ready_ack': True})

    @api.model
    def _cron_tracking_ready_repeat(self):
        """Ring again (like a pager) while the customer has not opened the page."""
        now = fields.Datetime.now()
        orders = self.sudo().search([
            ('tracking_state', '=', 'ready'),
            ('tracking_ready_ack', '=', False),
            ('tracking_ready_push_count', '>', 0),
            ('tracking_ready_date', '>=', now - timedelta(minutes=30)),
        ], limit=500)
        orders = orders.filtered(lambda o: o.tracking_ready_push_count < o.config_id.tracking_alert_repeat)
        due = orders.filtered(
            lambda o: not o.tracking_ready_last_push
            or o.tracking_ready_last_push <= now - timedelta(seconds=READY_REPEAT_SECONDS - 5))
        for order in due:
            order.write({
                'tracking_ready_push_count': order.tracking_ready_push_count + 1,
                'tracking_ready_last_push': now,
            })
        due._tracking_queue_push('ready')
        if orders.filtered(lambda o: o.tracking_ready_push_count < o.config_id.tracking_alert_repeat):
            self.env.ref('pos_order_tracking_nps.ir_cron_tracking_ready_repeat').sudo()._trigger(
                at=now + timedelta(seconds=READY_REPEAT_SECONDS))

    # ------------------------------------------------------------------
    # Manual actions (backend)
    # ------------------------------------------------------------------
    def action_tracking_mark_ready(self):
        now = fields.Datetime.now()
        for order in self:
            if not order.config_id.tracking_enabled:
                raise UserError(_("Order tracking is not enabled on %s.", order.config_id.name))
            notify = order.tracking_state in ('received', 'preparing')
            order.sudo().write({'tracking_state': 'ready', 'tracking_ready_date': order.tracking_ready_date or now})
            if notify:
                order._tracking_notify_ready()
        return True

    def action_tracking_mark_completed(self):
        now = fields.Datetime.now()
        for order in self:
            order.sudo().write({
                'tracking_state': 'completed',
                'tracking_ready_date': order.tracking_ready_date or now,
                'tracking_completed_date': now,
            })
        return True

    def action_open_tracking_page(self):
        self.ensure_one()
        self._tracking_ensure_token()
        return {'type': 'ir.actions.act_url', 'url': self.tracking_url, 'target': 'new'}

    # ------------------------------------------------------------------
    # Public page data
    # ------------------------------------------------------------------
    def _tracking_nps_available_at(self):
        """When the rating can be asked: N minutes after the order is ready (pager on)
        or after the order was placed (pager off)."""
        self.ensure_one()
        config = self.config_id
        if not config.tracking_nps_enabled:
            return False
        start = self.tracking_ready_date if config.tracking_pager_enabled else self.date_order
        if not start:
            return False
        return start + timedelta(minutes=config.tracking_nps_delay or 0)

    def _tracking_public_lines(self):
        self.ensure_one()
        lines = []
        for line in self.lines.filtered(lambda l: not l.combo_parent_id and l.qty > 0):
            lines.append({
                'name': line.full_product_name or line.product_id.display_name,
                'qty': line.qty,
                'items': [c.full_product_name or c.product_id.display_name for c in line.combo_line_ids],
            })
        return lines

    def _tracking_public_data(self):
        """Data exposed on the public tracking page (no prices, no customer data)."""
        self.ensure_one()
        config = self.config_id
        nps_at = self._tracking_nps_available_at()
        now = fields.Datetime.now()
        return {
            'state': self.tracking_state or 'received',
            'number': self.tracking_number or self.pos_reference or self.name,
            'reference': self.pos_reference or self.name,
            'date_order': fields.Datetime.to_string(self.date_order),
            'ready_date': fields.Datetime.to_string(self.tracking_ready_date) if self.tracking_ready_date else False,
            'server_now': fields.Datetime.to_string(now),
            'lines': self._tracking_public_lines(),
            'features': {'pager': config.tracking_pager_enabled, 'nps': config.tracking_nps_enabled},
            'nps': {
                'done': self.nps_score >= 0,
                'score': self.nps_score if self.nps_score >= 0 else None,
                'available': bool(nps_at) and nps_at <= now and self.nps_score < 0,
                'seconds_left': max(0, int((nps_at - now).total_seconds())) if nps_at else None,
            },
            'shop': {
                'name': self.company_id.name,
                'company_id': self.company_id.id,
                'pos_name': config.name,
                'color': config.tracking_color or '#0F766E',
                'ready_message': config.tracking_ready_message or '',
                'google_review_url': config.tracking_google_review_url or '',
            },
        }

    def _tracking_submit_nps(self, score, comment=None):
        self.ensure_one()
        order = self.sudo()
        if order.nps_score >= 0:
            return False
        nps_at = order._tracking_nps_available_at()
        if not nps_at or nps_at > fields.Datetime.now():
            return False
        order.env['pos.order.nps'].create({
            'order_id': order.id,
            'score': score,
            'comment': (comment or '').strip()[:2000] or False,
        })
        order.nps_score = score
        return True

    # ------------------------------------------------------------------
    # Web push
    # ------------------------------------------------------------------
    def _tracking_push_payload(self, kind):
        self.ensure_one()
        config = self.config_id
        number = self.tracking_number or self.pos_reference
        lang = (self.env.context.get('lang') or 'ar')[:2]
        if kind == 'ready':
            title = _("Your order #%s is ready!", number)
            body = config.tracking_ready_message or _("Please pick it up at the counter. Enjoy!")
        else:
            title = _("How was your order?")
            body = _("Tap to rate %s in 5 seconds.", self.company_id.name)
        return json.dumps({
            'title': title,
            'options': {
                'body': body,
                'tag': f"order-{self.tracking_token}-{kind}",
                'renotify': True,
                'requireInteraction': kind == 'ready',
                'silent': False,
                'vibrate': [500, 200, 500, 200, 500, 200, 900] if kind == 'ready' else [200, 100, 200],
                'icon': f"/logo.png?company={self.company_id.id}",
                'badge': f"/logo.png?company={self.company_id.id}",
                'timestamp': int(fields.Datetime.now().timestamp() * 1000),
                'data': {'url': f"/track/{self.tracking_token}", 'kind': kind, 'lang': lang,
                         'ack': f"/track/{self.tracking_token}/ack" if kind == 'ready' else False},
            },
        })

    def _tracking_installed_lang(self, prefix):
        """Best installed language for 'ar' / 'en' (falls back to the company partner language)."""
        installed = [code for code, _name in self.env['res.lang'].get_installed()]
        prefix = (prefix or 'ar')[:2]
        for code in installed:
            if code.startswith(prefix):
                return code
        fallback = self.company_id.partner_id.lang
        return fallback if fallback in installed else (installed[0] if installed else 'en_US')

    def _tracking_queue_push(self, kind):
        Push = self.env['pos.tracking.push'].sudo()
        vals_list = []
        for order in self.sudo():
            subscriptions = order.env['pos.tracking.subscription'].search([('order_id', '=', order.id)])
            for sub in subscriptions:
                lang = order._tracking_installed_lang(sub.lang)
                payload = order.with_context(lang=lang)._tracking_push_payload(kind)
                vals_list.append({
                    'subscription_id': sub.id,
                    'payload': payload,
                    # The ready alert must wake a locked phone now; the survey can wait.
                    'urgency': 'high' if kind == 'ready' else 'normal',
                    'ttl': 900 if kind == 'ready' else 3600,
                })
        if vals_list:
            Push.create(vals_list)
            self.env.ref('pos_order_tracking_nps.ir_cron_tracking_push').sudo()._trigger()

    @api.model
    def _cron_tracking_nps_reminder(self):
        """Push the NPS survey to subscribed customers once the survey is open."""
        now = fields.Datetime.now()
        subscribed = self.env['pos.tracking.subscription'].sudo().search([]).order_id
        orders = subscribed.filtered(
            lambda o: not o.tracking_nps_reminder_sent and o.nps_score < 0
            and o.config_id.tracking_nps_enabled
            and o.date_order and o.date_order >= now - timedelta(days=1)
        )
        due = orders.filtered(
            lambda o: o._tracking_is_eligible()
            and o._tracking_nps_available_at() and o._tracking_nps_available_at() <= now
        )
        if due:
            due.write({'tracking_nps_reminder_sent': True})
            due._tracking_queue_push('nps')
