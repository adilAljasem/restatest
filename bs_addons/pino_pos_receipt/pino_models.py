# -*- coding: utf-8 -*-
"""All server side models of the module, kept in a single file on purpose.

A flat layout means the module is only a handful of files at its root, which
makes it impossible to lose a sub-package while copying or committing it.
"""
import re

from odoo import api, fields, models

PINO_POS_FIELDS = [
    'pino_receipt_design',
    'pino_kitchen_design',
    'pino_show_latin_name',
    'pino_show_barcode',
    'pino_show_zatca_qr',
    'pino_receipt_slogan',
    'pino_receipt_website',
    'pino_receipt_thanks',
    'pino_kitchen_title',
]

# Arabic, Arabic Supplement and Arabic Extended-A blocks.
ARABIC_CHARS = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]')


class PosConfig(models.Model):
    _inherit = 'pos.config'

    # ------------------------------------------------------------------
    # Master switches
    # ------------------------------------------------------------------
    pino_receipt_design = fields.Boolean(
        string="Custom Sales Receipt Design",
        default=False,
        help="عند التفعيل تُطبع فاتورة المبيعات بالتصميم المخصص. "
             "عند الإيقاف يعود النظام إلى التصميم الافتراضي.",
    )
    pino_kitchen_design = fields.Boolean(
        string="Custom Kitchen Ticket Design",
        default=False,
        help="عند التفعيل يُطبع إيصال المطبخ (KOT) بالتصميم المخصص. "
             "عند الإيقاف يعود النظام إلى التصميم الافتراضي.",
    )

    # ------------------------------------------------------------------
    # Optional elements of the custom design
    # ------------------------------------------------------------------
    pino_show_latin_name = fields.Boolean(
        string="Show Latin Product Name",
        default=True,
        help="إظهار اسم الصنف باللغة اللاتينية أسفل الاسم العربي.",
    )
    pino_show_barcode = fields.Boolean(
        string="Show Receipt Barcode",
        default=True,
        help="طباعة باركود Code128 يحمل رقم الفاتورة أسفل الإيصال.",
    )
    pino_show_zatca_qr = fields.Boolean(
        string="Show ZATCA QR Box",
        default=True,
        help="إظهار صندوق رمز الاستجابة السريعة المعتمد من هيئة الزكاة والضريبة والجمارك.",
    )

    # ------------------------------------------------------------------
    # Free texts
    # ------------------------------------------------------------------
    pino_receipt_slogan = fields.Char(
        string="Receipt Slogan",
        default="غالي وسير على غالي",
        help="العبارة التسويقية أسفل الإيصال.",
    )
    pino_receipt_website = fields.Char(
        string="Receipt Website",
        help="الموقع الإلكتروني المطبوع على الإيصال. إذا تُرك فارغاً يُستخدم موقع الشركة.",
    )
    pino_receipt_thanks = fields.Char(
        string="Receipt Thanks Note",
        default="شكراً لزيارتكم – نراكم قريباً",
    )
    pino_kitchen_title = fields.Char(
        string="Kitchen Ticket Title",
        default="المطبخ – KITCHEN",
    )

    @api.model
    def _load_pos_data_fields(self, config):
        """Make sure the settings always reach the POS front-end.

        An empty list means "load every field", so it must be left untouched:
        turning it into an explicit list would drop every other field.
        """
        fields_list = super()._load_pos_data_fields(config)
        if not fields_list:
            return fields_list
        return list(fields_list) + [
            name for name in PINO_POS_FIELDS if name not in fields_list
        ]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    pino_name_latin = fields.Char(
        string="Latin Name (Receipt)",
        compute='_compute_pino_name_latin',
        store=True,
        readonly=False,
        help="الاسم اللاتيني المطبوع أسفل اسم الصنف في الفاتورة وإيصال المطبخ. "
             "يُقترح تلقائياً من ترجمة الاسم بالإنجليزية إن وُجدت، ويمكن كتابته أو تعديله يدوياً.",
    )

    def _pino_latin_candidate(self):
        """Return a Latin-script translation of the product name, if any exists.

        English is preferred, then any other installed language. A value written
        in Arabic is rejected: when a product was created in Arabic only, Odoo
        stores that same Arabic text under every language, and printing it twice
        is exactly what we want to avoid.
        """
        self.ensure_one()
        codes = self.env['res.lang'].search([]).mapped('code')
        ordered = (
            [code for code in codes if code.startswith('en')]
            + ['en_US']
            + [code for code in codes if not code.startswith('en')]
        )
        seen = set()
        for code in ordered:
            if code in seen:
                continue
            seen.add(code)
            value = (self.with_context(lang=code).name or '').strip()
            if value and not ARABIC_CHARS.search(value):
                return value
        return False

    @api.depends('name')
    def _compute_pino_name_latin(self):
        """Suggest a Latin name, but never overwrite a value entered by hand."""
        for template in self:
            if template.pino_name_latin:
                continue
            template.pino_name_latin = template._pino_latin_candidate()

    def action_pino_suggest_latin_names(self):
        """Re-run the suggestion on the selected products."""
        for template in self:
            candidate = template._pino_latin_candidate()
            if candidate:
                template.pino_name_latin = candidate
        return True

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if 'pino_name_latin' not in fields_list:
            fields_list = list(fields_list) + ['pino_name_latin']
        return fields_list


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_pino_receipt_design = fields.Boolean(
        related='pos_config_id.pino_receipt_design', readonly=False)
    pos_pino_kitchen_design = fields.Boolean(
        related='pos_config_id.pino_kitchen_design', readonly=False)
    pos_pino_show_latin_name = fields.Boolean(
        related='pos_config_id.pino_show_latin_name', readonly=False)
    pos_pino_show_barcode = fields.Boolean(
        related='pos_config_id.pino_show_barcode', readonly=False)
    pos_pino_show_zatca_qr = fields.Boolean(
        related='pos_config_id.pino_show_zatca_qr', readonly=False)
    pos_pino_receipt_slogan = fields.Char(
        related='pos_config_id.pino_receipt_slogan', readonly=False)
    pos_pino_receipt_website = fields.Char(
        related='pos_config_id.pino_receipt_website', readonly=False)
    pos_pino_receipt_thanks = fields.Char(
        related='pos_config_id.pino_receipt_thanks', readonly=False)
    pos_pino_kitchen_title = fields.Char(
        related='pos_config_id.pino_kitchen_title', readonly=False)
