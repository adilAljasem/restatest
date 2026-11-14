from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_tracking_enabled = fields.Boolean(related='pos_config_id.tracking_enabled', readonly=False)
    pos_tracking_kds_display_id = fields.Many2one(related='pos_config_id.tracking_kds_display_id', readonly=False)
    pos_tracking_kds_stage_id = fields.Many2one(related='pos_config_id.tracking_kds_stage_id', readonly=False)
    pos_tracking_stage_ids = fields.Many2many('resta.kds.stage', compute='_compute_pos_tracking_stage_ids')
    pos_tracking_nps_delay = fields.Integer(related='pos_config_id.tracking_nps_delay', readonly=False)
    pos_tracking_color = fields.Char(related='pos_config_id.tracking_color', readonly=False)
    pos_tracking_ready_message = fields.Char(related='pos_config_id.tracking_ready_message', readonly=False)
    pos_tracking_google_review_url = fields.Char(related='pos_config_id.tracking_google_review_url', readonly=False)
    pos_tracking_preset_ids = fields.Many2many(related='pos_config_id.tracking_preset_ids', readonly=False)
    pos_tracking_user_ids = fields.Many2many(related='pos_config_id.tracking_user_ids', readonly=False)
    pos_tracking_pager_enabled = fields.Boolean(related='pos_config_id.tracking_pager_enabled', readonly=False)
    pos_tracking_nps_enabled = fields.Boolean(related='pos_config_id.tracking_nps_enabled', readonly=False)
    pos_tracking_alert_repeat = fields.Integer(related='pos_config_id.tracking_alert_repeat', readonly=False)

    @api.depends('pos_tracking_kds_display_id')
    def _compute_pos_tracking_stage_ids(self):
        for settings in self:
            # The first stage receives new orders: it cannot be the ready stage.
            stages = self.env['pos.config']._tracking_sorted_stages(settings.pos_tracking_kds_display_id)
            settings.pos_tracking_stage_ids = stages[1:]

    @api.onchange('pos_tracking_kds_display_id')
    def _onchange_pos_tracking_kds_display_id(self):
        if self.pos_tracking_kds_stage_id not in self.pos_tracking_stage_ids:
            self.pos_tracking_kds_stage_id = False
