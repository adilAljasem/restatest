# -*- coding: utf-8 -*-
from odoo import api, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def _check_uom_not_in_invoice(self):
        """
        Override account's constraint: do not block changing the product's
        Unit of Measure when the product is already used in posted Journal Entries.
        This allows changing UoM at any time; existing move lines keep their
        original product_uom_id.
        """
        pass

    def write(self, vals):
        """When changing UoM, update only the product. Do not propagate to SO/PO or any other records."""
        if "uom_id" not in vals:
            return super().write(vals)
        uom_id = vals.pop("uom_id")
        res = True
        if vals:
            res = super().write(vals)
        self._write({"uom_id": uom_id})
        return res

    @api.onchange("uom_id")
    def _onchange_uom_id(self):
        """Do not show the 'What to expect ?' warning; UoM change does not update existing records."""
        return
