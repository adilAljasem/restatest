# -*- coding: utf-8 -*-
# Part of BrowseInfo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_button_layout = fields.Boolean(related='pos_config_id.button_layout', readonly=False)
    pos_column_layout = fields.Selection(related='pos_config_id.column_layout', readonly=False)
    pos_left_right_layout = fields.Selection(related='pos_config_id.left_right_layout', readonly=False) 
