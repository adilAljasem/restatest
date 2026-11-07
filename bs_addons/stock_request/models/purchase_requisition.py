# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import api, fields, models
from odoo.tools import str2bool

from .res_config_settings import ALLOW_PURCHASE_LINE_WAREHOUSE_PARAMETER


class PurchaseRequisition(models.Model):
    _inherit = "purchase.requisition"

    stock_request_multi_warehouse_enabled = fields.Boolean(
        compute="_compute_stock_request_multi_warehouse_enabled",
        string="Purchase Line Warehouses Enabled",
    )

    @api.model
    def _stock_request_multi_warehouse_is_enabled(self):
        return str2bool(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(ALLOW_PURCHASE_LINE_WAREHOUSE_PARAMETER, "False")
        )

    def _compute_stock_request_multi_warehouse_enabled(self):
        enabled = self._stock_request_multi_warehouse_is_enabled()
        for requisition in self:
            requisition.stock_request_multi_warehouse_enabled = enabled


class PurchaseRequisitionLine(models.Model):
    _inherit = "purchase.requisition.line"

    warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        string="Warehouse",
        check_company=True,
        ondelete="restrict",
        domain="[('company_id', '=', company_id)]",
        help=(
            "Optional warehouse that will receive the purchase order line. If "
            "empty, the purchase order's Deliver To operation type is used."
        ),
    )

    def _prepare_purchase_order_line(
        self,
        name,
        product_qty=0.0,
        price_unit=0.0,
        taxes_ids=False,
    ):
        values = super()._prepare_purchase_order_line(
            name,
            product_qty=product_qty,
            price_unit=price_unit,
            taxes_ids=taxes_ids,
        )
        if self.requisition_id._stock_request_multi_warehouse_is_enabled():
            values.update(
                {
                    "warehouse_id": self.warehouse_id.id or False,
                    "stock_request_requisition_line_id": self.id,
                }
            )
        return values
