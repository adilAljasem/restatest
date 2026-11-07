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
from lxml import etree

from odoo import api, fields, models
from odoo.tools import file_open


class PosReceipt(models.Model):
    """
        This is an Odoo model for Point of Sale (POS).
        It creates a new model of pos.receipt for providing different types of
        receipt design.
    """
    _name = 'pos.receipt'
    _description = 'POS Receipts'

    name = fields.Char(string='Name', help='Name of the pos receipt')
    design_receipt = fields.Text(string='Receipt XML',
                                 help='Add your customised receipts for pos')

    @api.model
    def migrate_bilingual_thermal_receipt(self, receipt_id):
        """Refresh the module-owned receipt template and label on upgrade."""
        receipt = self.browse(receipt_id).exists()
        if not receipt:
            return
        with file_open('odoo_pos_custom_branding/data/bilingual_thermal_receipt_data.xml', 'rb') as source:
            record = etree.parse(source).xpath(".//record[@id='bilingual_thermal_receipt']")[0]
        template = record.xpath("./field[@name='design_receipt']")[0].text
        name = record.xpath("./field[@name='name']")[0].text
        values = {}
        if receipt.design_receipt != template:
            values['design_receipt'] = template
        if receipt.name != name:
            values['name'] = name
        if values:
            receipt.write(values)
