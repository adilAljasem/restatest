# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import api, models


class ReportStockRequestOrder(models.AbstractModel):
    _name = "report.stock_request.report_stock_request_order_document"
    _description = "Stock Request / Internal Transfer Report"

    @api.model
    def _get_report_values(self, docids, data=None):
        orders = self.env["stock.request.order"].browse(docids).exists()
        orders._check_print_stock_request_access()
        return {
            "doc_ids": orders.ids,
            "doc_model": "stock.request.order",
            "docs": orders,
        }
