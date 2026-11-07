from odoo import api, fields, models


def _installed_lang(env, code):
    """Return `code` when that language is active, else False (reading a
    record with an inactive language raises "Invalid language code")."""
    return code if code in dict(env['res.lang'].get_installed()) else False


def _names_in(records, code):
    records = records.filtered(lambda record: isinstance(record.id, int))  # skip unsaved
    lang = records and _installed_lang(records.env, code)
    if not lang:
        return {}
    return {row['id']: row['name'] for row in records.with_context(lang=lang).read(['name'])}


def _read_pos_receipt_translations(templates):
    names_ar = _names_in(templates, 'ar_001')
    names_en = _names_in(templates, 'en_US')
    return {
        record.id: {
            'ar': names_ar.get(record.id) or '',
            'en': names_en.get(record.id) or '',
        }
        for record in templates
    }


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    name_en = fields.Char(
        string='English Name',
        compute='_compute_translated_receipt_names',
        compute_sudo=False,
    )
    name_ar = fields.Char(
        string='Arabic Name',
        compute='_compute_translated_receipt_names',
        compute_sudo=False,
    )

    @api.depends('name')
    def _compute_translated_receipt_names(self):
        if not self:
            return
        english_names = _names_in(self, 'en_US')
        arabic_names = _names_in(self, 'ar_001')
        for record in self:
            record.name_en = english_names.get(record.id) or record.name or ''
            record.name_ar = arabic_names.get(record.id) or record.name or ''

    @api.model
    def _load_pos_data_fields(self, config_id):
        # Odoo 18 loads product.template in the POS with only ['id']; add the
        # translated names used by the Arabic receipts (product_tmpl_id.name_ar).
        fields_list = list(super()._load_pos_data_fields(config_id))
        for field_name in ('name_en', 'name_ar'):
            if field_name not in fields_list:
                fields_list.append(field_name)
        return fields_list
