# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    cc_terminal_center_id = fields.Many2one(
        'pos.call.center', string='Call Center POS of', index='btree_not_null', copy=False,
        help='Set when this Point of Sale is used by call center agents to take orders.')
    cc_is_terminal = fields.Boolean(
        'Is Call Center POS', compute='_compute_cc_is_terminal', store=True)
    cc_is_branch = fields.Boolean(
        'Receives Call Center Orders', compute='_compute_cc_is_branch')
    cc_auto_send_kitchen = fields.Boolean(compute='_compute_cc_is_branch')

    @api.depends('cc_terminal_center_id')
    def _compute_cc_is_terminal(self):
        for config in self:
            config.cc_is_terminal = bool(config.cc_terminal_center_id)

    def _compute_cc_is_branch(self):
        Center = self.env['pos.call.center'].sudo()
        for config in self:
            centers = Center._get_centers_for_branch(config) if config.id else Center
            config.cc_is_branch = bool(centers)
            config.cc_auto_send_kitchen = any(centers.mapped('auto_send_kitchen'))
