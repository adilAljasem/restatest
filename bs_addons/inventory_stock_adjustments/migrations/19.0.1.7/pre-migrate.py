from odoo.tools.sql import column_exists, rename_column


def migrate(cr, version):
    # Preserve the original per-location quantity, including validated history.
    if (column_exists(cr, 'stock_inventory_line', 'quantity')
            and not column_exists(cr, 'stock_inventory_line', 'location_quantity')):
        rename_column(cr, 'stock_inventory_line', 'quantity', 'location_quantity')
