from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class PosConfig(models.Model):
    _inherit = 'pos.config'

    tracking_enabled = fields.Boolean(
        string="Order Tracking QR",
        help="Print a QR code on the customer receipt that opens a live order-status page.",
    )
    tracking_pager_enabled = fields.Boolean(
        string="Pager", default=True,
        help="Live order status on the customer's phone with an alert (sound, vibration, notification) when the order is ready.",
    )
    tracking_nps_enabled = fields.Boolean(
        string="Rating (NPS)", default=True,
        help="Ask the customer to rate the visit (0-10) and store the answer in Odoo.",
    )
    tracking_kds_display_id = fields.Many2one(
        'resta.kds.display',
        string="Tracking Screen",
        help="Kitchen screen that drives the customer status. When the order is ready on this screen "
             "the customer is notified. Leave empty to follow every kitchen screen linked to this "
             "point of sale (the order is ready when all of them are).",
    )
    tracking_kds_stage_id = fields.Many2one(
        'resta.kds.stage',
        string="Ready Stage",
        help="Stage of the tracking screen from which the order is shown as ready to the customer. "
             "Leave empty: the screen's own 'ready' stage, or the «Done» tap on a one-stage screen.",
    )
    tracking_preset_ids = fields.Many2many(
        'pos.preset', 'pos_config_tracking_preset_rel', 'config_id', 'preset_id',
        string="Tracking Order Types",
        help="Only orders of these types (e.g. Dine In) get the tracking QR. Leave empty for all order types.",
    )
    tracking_user_ids = fields.Many2many(
        'res.users', 'pos_config_tracking_user_rel', 'config_id', 'user_id',
        string="Tracking Cashiers",
        help="Only orders taken by these cashiers get the tracking QR. Leave empty for all cashiers.",
    )
    tracking_alert_repeat = fields.Integer(
        string="Ring Times", default=3,
        help="How many times the 'order ready' notification is sent (one minute apart) "
             "until the customer opens the page.",
    )
    tracking_nps_delay = fields.Integer(
        string="NPS Survey Delay (min)", default=30,
        help="Minutes after the order is ready before the customer is asked to rate the experience.",
    )
    tracking_color = fields.Char(string="Tracking Page Color", default="#0F766E")
    tracking_ready_message = fields.Char(
        string="Ready Message", translate=True,
        help="Text of the notification sent when the order is ready. Leave empty for the default text.",
    )
    tracking_google_review_url = fields.Char(
        string="Google Review Link",
        help="Promoters (score 9-10) are invited to review you on Google Maps with this link.",
    )

    @api.constrains('tracking_nps_delay', 'tracking_alert_repeat')
    def _check_tracking_nps_delay(self):
        for config in self:
            if config.tracking_nps_delay < 0:
                raise ValidationError(_("The NPS survey delay cannot be negative."))
            if not 1 <= config.tracking_alert_repeat <= 10:
                raise ValidationError(_("The ready alert can be sent between 1 and 10 times."))

    @api.constrains('tracking_enabled', 'tracking_pager_enabled', 'tracking_nps_enabled')
    def _check_tracking_features(self):
        for config in self:
            if config.tracking_enabled and not (config.tracking_pager_enabled or config.tracking_nps_enabled):
                raise ValidationError(_("Order tracking needs the pager, the rating (NPS), or both."))

    @api.constrains('tracking_kds_stage_id', 'tracking_kds_display_id')
    def _check_tracking_ready_stage(self):
        for config in self:
            stage = config.tracking_kds_stage_id
            if not stage:
                continue
            display = config.tracking_kds_display_id
            if not display:
                raise ValidationError(_("Choose the tracking screen before its ready stage."))
            stage_ids = self._tracking_sorted_stages(display).ids
            if stage.id not in stage_ids:
                raise ValidationError(_("The ready stage must belong to the tracking screen %s.", display.name))
            if stage_ids.index(stage.id) == 0:
                raise ValidationError(_("The ready stage cannot be the first stage of the screen: "
                                        "new orders arrive on it."))

    @api.model
    def _tracking_sorted_stages(self, display):
        return display.stage_ids.sorted(lambda s: (s.sequence, s.id))

    def _tracking_stage_levels(self, display):
        """(ordered stage ids, ready index, picked-up index) for one kitchen screen.

        * index 0 (where new orders arrive) is never ready nor picked up;
        * ready: the configured ready stage, else the screen's first «ready» stage; when there is
          none (the simple one-column screen) the order is ready when the cook taps «Done»;
        * picked up: a last stage after the ready stage, or «Done» tapped on the ready stage.
        """
        self.ensure_one()
        stages = self._tracking_sorted_stages(display)
        stage_ids = stages.ids
        ready_index = None
        configured = self.tracking_kds_stage_id
        if configured and configured.id in stage_ids and stage_ids.index(configured.id) > 0:
            ready_index = stage_ids.index(configured.id)
        else:
            for index, stage in enumerate(stages):
                if index and stage.is_ready:
                    ready_index = index
                    break
        last = len(stage_ids) - 1
        done_index = last if ready_index is not None and last > ready_index else None
        return stage_ids, ready_index, done_index

    def _tracking_order_eligible(self, order):
        """Whether the tracking (QR, status, notifications, NPS) applies to ``order``.

        Keep in sync with ``showTrackingQr`` in static/src/pos/pos_order_tracking.js.
        """
        self.ensure_one()
        if not self.tracking_enabled:
            return False
        if self.use_presets and self.tracking_preset_ids and order.preset_id not in self.tracking_preset_ids:
            return False
        return self._tracking_cashier_eligible(order)

    def _tracking_cashier_eligible(self, order):
        if self.module_pos_hr:
            # Employee login: handled by pos_order_tracking_nps_hr (filter on employees).
            return True
        return not self.tracking_user_ids or order.user_id in self.tracking_user_ids

    def _tracking_kds_displays(self):
        """Kitchen screens whose tickets decide the customer-facing status."""
        self.ensure_one()
        if self.tracking_kds_display_id:
            return self.tracking_kds_display_id
        return self.env['resta.kds.display'].sudo().search([('pos_config_ids', 'in', self.id)])
