from odoo import api, fields, models


def _installed(env, code):
    return code in dict(env['res.lang'].get_installed())


def _names(records, code):
    """{id: name} in language `code`; {} when that language is not active."""
    records = records.filtered(lambda r: isinstance(r.id, int))
    if not records or not _installed(records.env, code):
        return {}
    return {row['id']: row['name'] for row in records.with_context(lang=code).read(['name'])}


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    kt_name_ar = fields.Char(string='Arabic Name (Kitchen)', compute='_compute_kt_names')
    kt_name_en = fields.Char(string='English Name (Kitchen)', compute='_compute_kt_names')

    @api.depends('name')
    def _compute_kt_names(self):
        arabic = _names(self, 'ar_001')
        english = _names(self, 'en_US')
        for record in self:
            record.kt_name_ar = arabic.get(record.id) or ''
            record.kt_name_en = english.get(record.id) or ''

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = list(super()._load_pos_data_fields(config))
        for name in ('kt_name_ar', 'kt_name_en'):
            if fields_list and name not in fields_list:
                fields_list.append(name)
        return fields_list


class ProductTemplateAttributeValue(models.Model):
    _inherit = 'product.template.attribute.value'

    kt_name_ar = fields.Char(string='Arabic Name (Kitchen)', compute='_compute_kt_names')
    kt_name_en = fields.Char(string='English Name (Kitchen)', compute='_compute_kt_names')

    @api.depends('product_attribute_value_id.name')
    def _compute_kt_names(self):
        values = self.product_attribute_value_id
        arabic = _names(values, 'ar_001')
        english = _names(values, 'en_US')
        for record in self:
            value_id = record.product_attribute_value_id.id
            record.kt_name_ar = arabic.get(value_id) or ''
            record.kt_name_en = english.get(value_id) or ''

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = list(super()._load_pos_data_fields(config))
        for name in ('kt_name_ar', 'kt_name_en'):
            if fields_list and name not in fields_list:
                fields_list.append(name)
        return fields_list
