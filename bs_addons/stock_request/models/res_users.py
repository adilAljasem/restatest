# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import _, fields, models
from odoo.exceptions import ValidationError


class ResUsers(models.Model):
    _inherit = "res.users"

    _default_warehouse_field = "default_stock_request_warehouse_id"

    default_stock_request_warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        string="Default Request Warehouse",
        compute="_compute_default_stock_request_warehouse_id",
        inverse="_inverse_default_stock_request_warehouse_id",
        check_company=True,
        help="Destination warehouse proposed on new Stock Requests.",
    )

    def _compute_default_stock_request_warehouse_id(self):
        defaults = self.env["ir.default"].sudo()
        warehouses = self.env["stock.warehouse"].sudo()
        for user in self:
            warehouse_id = defaults._get(
                "res.users",
                self._default_warehouse_field,
                user_id=user.id,
            )
            user.default_stock_request_warehouse_id = warehouses.browse(
                warehouse_id
            ).exists()

    def _inverse_default_stock_request_warehouse_id(self):
        defaults = self.env["ir.default"].sudo()
        for user in self:
            warehouse_company = user.default_stock_request_warehouse_id.company_id
            if warehouse_company and warehouse_company not in user.company_ids:
                raise ValidationError(
                    _(
                        "The default request warehouse must belong to one of the "
                        "user's allowed companies."
                    )
                )
            defaults.set(
                "res.users",
                self._default_warehouse_field,
                user.default_stock_request_warehouse_id.id or False,
                user_id=user.id,
            )
