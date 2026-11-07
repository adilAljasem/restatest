from odoo import models


class StockMove(models.Model):
    _inherit = "stock.move"

    def _get_account_move_line_vals(self):
        line_values = super()._get_account_move_line_vals()
        analytic_account = self.scrap_id.batch_scrap_order_id.analytic_account_id
        loss_account = self.location_dest_id.valuation_account_id
        if not analytic_account or not loss_account or self.location_id.valuation_account_id:
            return line_values

        for values in line_values:
            # The destination loss leg is the debit; leave the stock credit alone.
            if values["account_id"] == loss_account.id and not values.get("credit"):
                values["analytic_distribution"] = {str(analytic_account.id): 100.0}
        return line_values
