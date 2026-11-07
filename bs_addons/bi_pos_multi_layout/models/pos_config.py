# -*- coding: utf-8 -*-
# Part of BrowseInfo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class POSConfigLock(models.Model):
    _inherit = 'pos.config'

    button_layout = fields.Boolean('Allow Button Layout')
    column_layout = fields.Selection([
        ('default', 'Default'),
        ('one', '1'),
        ('two', '2'),
        ('three', '3')
    ], string='Column Layout', default='default')
    left_right_layout = fields.Selection([
        ('default', 'Default'),
        ('left_side', 'Left Side'),
        ('right_side', 'Right Side')
    ], string='Left Right Layout', default='default')
