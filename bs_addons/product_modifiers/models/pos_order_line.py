# -*- coding: utf-8 -*-
# Powered by Kanak Infosystems LLP.
# © 2020 Kanak Infosystems LLP. (<https://www.kanakinfosystems.com>).

from odoo import api, fields, models


class POSOrderLine(models.Model):
    _inherit = 'pos.order.line'

    parent_line_id = fields.Char('Parent line id')
    portion = fields.Char('portion')
    modifier_line_id = fields.Integer('Modifier configuration line id')
    modifier_selection_qty = fields.Float(
        'Modifier selection quantity',
        help='Unscaled selector quantity used when a modifier applies to a portion.',
    )

    @api.model
    def _load_pos_data_fields(self, config):
        fields_to_load = super()._load_pos_data_fields(config)
        return list(dict.fromkeys([
            *fields_to_load,
            'parent_line_id',
            'portion',
            'modifier_line_id',
            'modifier_selection_qty',
        ]))
