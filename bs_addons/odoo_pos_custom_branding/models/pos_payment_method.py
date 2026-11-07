# -*- coding: utf-8 -*-
from odoo import api, fields, models

from .product_template import _read_pos_receipt_translations


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    receipt_name_ar = fields.Char(
        string='Arabic Name (Receipt)',
        compute='_compute_receipt_name_ar',
        help='Arabic translation of the payment method, printed as "الدفع - …".',
    )

    @api.depends('name')
    def _compute_receipt_name_ar(self):
        translations = _read_pos_receipt_translations(self)
        for method in self:
            method.receipt_name_ar = translations.get(method.id, {}).get('ar') or method.name or ''

    @api.model
    def _load_pos_data_fields(self, config_id):
        fields_list = list(super()._load_pos_data_fields(config_id))
        if fields_list and 'receipt_name_ar' not in fields_list:
            fields_list.append('receipt_name_ar')
        return fields_list
