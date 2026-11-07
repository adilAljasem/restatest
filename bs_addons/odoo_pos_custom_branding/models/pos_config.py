# -*- coding: utf-8 -*-
################################################################################
#
#    Axiom Cloudnest Technologies
#
#    Copyright (C) 2026-TODAY Axiom Cloudnest Technologies.
#    Author: Axiom Cloudnest Technologies
#
#    This program is free software: you can modify
#    it under the terms of the GNU Affero General Public License (AGPL) as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
################################################################################
from odoo import fields, models


class PosConfig(models.Model):
    """
        This is an Odoo model for Point of Sale (POS).
        It inherits the 'pos.config' model to add new fields.
    """
    _inherit = 'pos.config'

    receipt_design_id = fields.Many2one('pos.receipt', string='Receipt Design',
                                     help='Choose any receipt design')
    design_receipt = fields.Text(related='receipt_design_id.design_receipt',
                                 string='Receipt XML')
    logo = fields.Binary(related='company_id.logo', string='Logo',
                         readonly=False)
    is_custom_receipt = fields.Boolean(string='Is Custom Receipt',
                                       help='Indicates the receipt  design is '
                                            'custom or not')

    hide_odoo_branding = fields.Boolean(
        string='Hide Odoo Branding',
        help='Hide "Powered by Odoo" and Odoo logos from the POS receipt, '
             'header, customer display and lock screen.'
    )
    pos_brand_logo = fields.Binary(
        string='POS Brand Logo',
        help='Custom logo used in the POS header, customer screen and lock '
             'screen instead of the default Odoo logo.',
        attachment=True,
    )

    def _pos_ui_config(self):
        """Override to include custom branding fields in POS frontend"""
        result = super()._pos_ui_config()
        result['hide_odoo_branding'] = self.hide_odoo_branding
        result['pos_brand_logo'] = self.pos_brand_logo
        return result

    def read(self, fields=None, load='_classic_read'):
        """Override to include custom branding fields when config is read"""
        result = super().read(fields=fields, load=load)
        
        # If reading all fields or our custom fields are requested, include them
        if fields is None or 'hide_odoo_branding' in fields or 'pos_brand_logo' in fields:
            for i, record in enumerate(result):
                config_record = self.browse(record['id'])
                if fields is None or 'hide_odoo_branding' in fields:
                    record['hide_odoo_branding'] = config_record.hide_odoo_branding
                if fields is None or 'pos_brand_logo' in fields:
                    record['pos_brand_logo'] = config_record.pos_brand_logo
        
        return result
