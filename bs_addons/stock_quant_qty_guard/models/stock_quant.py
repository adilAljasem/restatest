import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# Float columns on stock_quant that the ORM shows as 0 when NULL,
# but raw SQL (reports, crons, server actions) receives as None.
_GUARDED_COLUMNS = ('quantity', 'reserved_quantity')


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    def init(self):
        """Runs on install/upgrade: clean existing NULLs and add a DB default
        so rows inserted without these columns get 0 instead of NULL."""
        super().init()
        cr = self.env.cr
        for col in _GUARDED_COLUMNS:
            cr.execute(f"UPDATE stock_quant SET {col} = 0 WHERE {col} IS NULL")
            if cr.rowcount:
                _logger.info("stock_quant_qty_guard: fixed %s quant(s) with NULL %s", cr.rowcount, col)
            cr.execute(f"ALTER TABLE stock_quant ALTER COLUMN {col} SET DEFAULT 0")

    @api.model_create_multi
    def create(self, vals_list):
        # Don't inject values into vals: inventory-mode create only accepts a
        # whitelist of fields. Patch the inserted rows afterwards instead.
        quants = super().create(vals_list)
        self._qty_guard_fix_nulls(quants.ids)
        return quants

    def _apply_inventory(self, *args, **kwargs):
        res = super()._apply_inventory(*args, **kwargs)
        self._qty_guard_fix_nulls(self.ids)
        return res

    @api.model
    def _qty_guard_fix_nulls(self, ids):
        if not ids:
            return
        cr = self.env.cr
        for col in _GUARDED_COLUMNS:
            cr.execute(f"UPDATE stock_quant SET {col} = 0 WHERE id IN %s AND {col} IS NULL", (tuple(ids),))
