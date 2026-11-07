# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_bilingual_receipt_brand_name = fields.Char(
        related='pos_config_id.bilingual_receipt_brand_name',
        readonly=False,
    )
    pos_bilingual_receipt_branch_display_name = fields.Char(
        related='pos_config_id.bilingual_receipt_branch_display_name',
        readonly=False,
    )
    pos_receipt_thank_you_msg = fields.Char(
        related='pos_config_id.receipt_thank_you_msg',
        readonly=False,
    )
    pos_receipt_show_zatca_qr = fields.Boolean(
        related='pos_config_id.receipt_show_zatca_qr',
        readonly=False,
    )
    pos_receipt_show_item_count = fields.Boolean(
        related='pos_config_id.receipt_show_item_count',
        readonly=False,
    )
    pos_receipt_show_opened_by = fields.Boolean(
        related='pos_config_id.receipt_show_opened_by',
        readonly=False,
    )
    pos_receipt_show_closed_by = fields.Boolean(
        related='pos_config_id.receipt_show_closed_by',
        readonly=False,
    )

