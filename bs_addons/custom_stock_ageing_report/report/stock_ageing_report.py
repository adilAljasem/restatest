from odoo import models


class StockAgeingReport(models.AbstractModel):
    _name = "report.custom_stock_ageing_report.report_stock_ageing_document"
    _description = "Stock Ageing PDF Report"

    def _get_report_values(self, docids, data=None):
        wizards = self.env["stock.ageing.report.wizard"].browse(docids).exists()
        reports = []
        for wizard in wizards:
            reports.append({
                "wizard": wizard,
                "data": wizard.result_data or self.env["stock.ageing.engine"]._get_report_data(wizard),
            })
        return {
            "doc_ids": wizards.ids,
            "doc_model": "stock.ageing.report.wizard",
            "docs": wizards,
            "reports": reports,
        }
