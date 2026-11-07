# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    stock_request_transit_location_id = fields.Many2one(
        comodel_name="stock.location",
        string="Stock Request Transit Location",
        check_company=True,
        ondelete="restrict",
        domain="[('usage', '=', 'transit'), '|', ('company_id', '=', False), "
        "('company_id', '=', id)]",
        help="Transit location used between the delivery and receipt transfers.",
    )

    @api.constrains("stock_request_transit_location_id")
    def _check_stock_request_transit_location(self):
        for company in self.filtered("stock_request_transit_location_id"):
            location = company.stock_request_transit_location_id
            if location.usage != "transit":
                raise ValidationError(
                    _("The Stock Request Transit Location must be a Transit Location.")
                )
            if location.company_id and location.company_id != company:
                raise ValidationError(
                    _(
                        "The Stock Request Transit Location must belong to the "
                        "same company."
                    )
                )
