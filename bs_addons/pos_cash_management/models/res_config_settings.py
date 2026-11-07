# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_pcm_require_count = fields.Boolean(
        related='pos_config_id.pcm_require_count', readonly=False)
    pos_pcm_blind_count = fields.Boolean(
        related='pos_config_id.pcm_blind_count', readonly=False)
    pos_pcm_auto_transfer = fields.Boolean(
        related='pos_config_id.pcm_auto_transfer', readonly=False)
    pos_pcm_transfer_reason = fields.Char(
        related='pos_config_id.pcm_transfer_reason', readonly=False)
    pos_pcm_difference_partner = fields.Boolean(
        related='pos_config_id.pcm_difference_partner', readonly=False)
