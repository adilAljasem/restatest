# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).


def migrate(cr, version):
    """Backfill line warehouses from the purchase order receipt operation."""
    cr.execute(
        """
        UPDATE purchase_order_line AS line
           SET warehouse_id = picking_type.warehouse_id
          FROM purchase_order AS purchase,
               stock_picking_type AS picking_type
         WHERE line.order_id = purchase.id
           AND purchase.picking_type_id = picking_type.id
           AND line.warehouse_id IS NULL
           AND line.product_id IS NOT NULL
           AND picking_type.warehouse_id IS NOT NULL
        """
    )
