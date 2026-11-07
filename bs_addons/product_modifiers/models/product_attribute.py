# -*- coding: utf-8 -*-
# Powered by Kanak Infosystems LLP.
# © 2020 Kanak Infosystems LLP. (<https://www.kanakinfosystems.com>).

from odoo import api, fields, models


class ProductAttribute(models.Model):
    _inherit = 'product.attribute'

    is_modifier = fields.Boolean('Is Modifier Attribute')

    @api.model
    def _load_pos_data_fields(self, config):
        fields_to_load = super()._load_pos_data_fields(config)
        return list(dict.fromkeys([*fields_to_load, 'is_modifier']))
