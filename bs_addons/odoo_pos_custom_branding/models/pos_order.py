# -*- coding: utf-8 -*-
from odoo import api, models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    @api.model
    def _load_pos_data_fields(self, config_id):
        fields_list = list(super()._load_pos_data_fields(config_id))
        if not fields_list:
            return fields_list
        for field_name in (
            'tracking_number',
            'pos_order_type_name',
            'pos_reference',
            # Delivery aggregator fields (FeedUs connector), only when installed.
            'aggregator',
            'aggregator_order_id',
            'feedus_order_id',
            # Delivery app details (bs_pos_delivery_core), see pos_order_delivery.py.
            'receipt_delivery_platform',
            'receipt_delivery_external_id',
            'receipt_delivery_reference',
        ):
            if field_name in self._fields and field_name not in fields_list:
                fields_list.append(field_name)
        return fields_list



# Fields of the two modifier modules the receipt reads to print a modifier
# under its item (only loaded when the module that defines them is installed).
# Links to other order lines and plain values only: the related models of the
# other fields are loaded by the modifier modules themselves.
MODIFIER_LINE_FIELDS = (
    # pos_restaurant_combo_modifiers
    'is_modifier_line',
    'modifier_parent_line_id',
    'modifier_child_line_ids',
    'modifier_option_name',
    'modifier_qty_per_parent',
    'modifier_instruction_data',
    # product_modifiers
    'modifier_parent_uuid',
    'parent_line_id',
    'portion',
)


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = list(super()._load_pos_data_fields(config))
        if not fields_list:
            return fields_list
        for field_name in MODIFIER_LINE_FIELDS:
            field = self._fields.get(field_name)
            if not field or field_name in fields_list:
                continue
            if field.relational and field.comodel_name != 'pos.order.line':
                continue
            fields_list.append(field_name)
        return fields_list
