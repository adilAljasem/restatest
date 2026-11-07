# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    bilingual_receipt_brand_name = fields.Char(
        string='Receipt Brand Name',
        help='Customer-facing brand name on the receipt; the legal company stays in the tax details.',
    )
    bilingual_receipt_branch_display_name = fields.Char(
        string='Receipt Branch Display Name',
        help='Customer-facing branch name, independent of the warehouse name.',
    )
    bilingual_receipt_branch_name = fields.Char(
        string='Branch Name (Receipt)',
        related='warehouse_id.name',
        readonly=True,
    )
    bilingual_receipt_branch_address = fields.Char(
        string='Branch Address (Receipt)',
        compute='_compute_bilingual_receipt_branch_contact',
        readonly=True,
    )
    bilingual_receipt_branch_phone = fields.Char(
        string='Branch Phone (Receipt)',
        compute='_compute_bilingual_receipt_branch_contact',
        readonly=True,
    )
    receipt_thank_you_msg = fields.Char(
        string='Receipt Thank-you Message',
        default='*** نسعد بخدمتكم ***',
        translate=False,
        help='Bilingual thank-you message printed at the bottom of the receipt.',
    )
    receipt_show_zatca_qr = fields.Boolean(
        string='Show ZATCA QR Code',
        default=True,
        help='Print a ZATCA-compliant QR code at the bottom of every receipt.',
    )
    receipt_show_item_count = fields.Boolean(
        string='Show Item Count',
        default=True,
        help='Display the total number of items at the bottom of the receipt.',
    )

    @api.depends(
        'warehouse_id',
        'warehouse_id.partner_id',
        'warehouse_id.partner_id.street',
        'warehouse_id.partner_id.street2',
        'warehouse_id.partner_id.city',
        'warehouse_id.partner_id.zip',
        'warehouse_id.partner_id.phone',
    )
    def _compute_bilingual_receipt_branch_contact(self):
        for config in self:
            partner = config.warehouse_id.partner_id
            if not partner:
                config.bilingual_receipt_branch_address = ''
                config.bilingual_receipt_branch_phone = ''
                continue

            address_parts = [
                partner.street,
                partner.street2,
                partner.city,
                partner.zip,
            ]
            address_parts = [
                str(part).strip()
                for part in address_parts
                if part and str(part).strip()
            ]
            config.bilingual_receipt_branch_address = '، '.join(address_parts)
            config.bilingual_receipt_branch_phone = (partner.phone or '').strip()
