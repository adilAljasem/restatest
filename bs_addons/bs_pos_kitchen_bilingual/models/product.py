from odoo import api, fields, models


ARABIC_LANG = "ar_001"
ENGLISH_LANG = "en_US"


def _translated_values(records, field_name, lang):
    """Read a translated field in batch and index it by record id."""
    if not records:
        return {}
    return {
        row["id"]: row[field_name]
        for row in records.with_context(lang=lang).read([field_name])
    }


class ProductProduct(models.Model):
    _inherit = "product.product"

    kitchen_name_ar = fields.Char(
        string="Arabic Kitchen Name",
        compute="_compute_kitchen_names",
    )
    kitchen_name_en = fields.Char(
        string="English Kitchen Name",
        compute="_compute_kitchen_names",
    )

    @api.depends("product_tmpl_id.name")
    def _compute_kitchen_names(self):
        templates = self.product_tmpl_id
        names_ar = _translated_values(templates, "name", ARABIC_LANG)
        names_en = _translated_values(templates, "name", ENGLISH_LANG)
        for product in self:
            fallback = product.product_tmpl_id.name or product.display_name or ""
            product.kitchen_name_ar = names_ar.get(product.product_tmpl_id.id) or fallback
            product.kitchen_name_en = names_en.get(product.product_tmpl_id.id) or fallback

    @api.model
    def _load_pos_data_fields(self, config):
        result = super()._load_pos_data_fields(config)
        for field_name in ("kitchen_name_ar", "kitchen_name_en"):
            if field_name not in result:
                result.append(field_name)
        return result


class ProductAttribute(models.Model):
    _inherit = "product.attribute"

    kitchen_name_ar = fields.Char(
        string="Arabic Kitchen Name",
        compute="_compute_kitchen_names",
    )
    kitchen_name_en = fields.Char(
        string="English Kitchen Name",
        compute="_compute_kitchen_names",
    )

    @api.depends("name")
    def _compute_kitchen_names(self):
        names_ar = _translated_values(self, "name", ARABIC_LANG)
        names_en = _translated_values(self, "name", ENGLISH_LANG)
        for attribute in self:
            fallback = attribute.name or ""
            attribute.kitchen_name_ar = names_ar.get(attribute.id) or fallback
            attribute.kitchen_name_en = names_en.get(attribute.id) or fallback

    @api.model
    def _load_pos_data_fields(self, config):
        result = super()._load_pos_data_fields(config)
        for field_name in ("kitchen_name_ar", "kitchen_name_en"):
            if field_name not in result:
                result.append(field_name)
        return result


class ProductTemplateAttributeValue(models.Model):
    _inherit = "product.template.attribute.value"

    kitchen_name_ar = fields.Char(
        string="Arabic Kitchen Name",
        compute="_compute_kitchen_names",
    )
    kitchen_name_en = fields.Char(
        string="English Kitchen Name",
        compute="_compute_kitchen_names",
    )

    @api.depends("name")
    def _compute_kitchen_names(self):
        names_ar = _translated_values(self, "name", ARABIC_LANG)
        names_en = _translated_values(self, "name", ENGLISH_LANG)
        for value in self:
            fallback = value.name or ""
            value.kitchen_name_ar = names_ar.get(value.id) or fallback
            value.kitchen_name_en = names_en.get(value.id) or fallback

    @api.model
    def _load_pos_data_fields(self, config):
        result = super()._load_pos_data_fields(config)
        for field_name in ("kitchen_name_ar", "kitchen_name_en"):
            if field_name not in result:
                result.append(field_name)
        return result

