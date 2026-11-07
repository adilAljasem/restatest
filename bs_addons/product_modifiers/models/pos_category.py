# -*- coding: utf-8 -*-
# Powered by Kanak Infosystems LLP.
# © 2020 Kanak Infosystems LLP. (<https://www.kanakinfosystems.com>).

from odoo import api, fields, models


class PosCateory(models.Model):
    _inherit = 'pos.category'

    is_modifier = fields.Boolean('Is Modifier Category?')
    modifier_ids = fields.One2many('pos.modifiers', 'pos_categoryid', string='Modifiers')

    @api.model
    def _load_pos_data_fields(self, config):
        fields_to_load = super()._load_pos_data_fields(config)
        return list(dict.fromkeys([*fields_to_load, 'is_modifier', 'modifier_ids']))
