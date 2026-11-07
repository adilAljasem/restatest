# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).


def migrate(cr, version):
    """Backfill the new warehouse-derived source on existing requests."""
    cr.execute(
        """
        UPDATE stock_request_order AS request_order
           SET source_warehouse_id = request_order.warehouse_id
         WHERE request_order.source_warehouse_id IS NULL
        """
    )
    cr.execute(
        """
        UPDATE stock_request_order AS request_order
           SET source_location_id = warehouse.lot_stock_id
          FROM stock_warehouse AS warehouse
         WHERE warehouse.id = request_order.source_warehouse_id
           AND request_order.source_location_id IS DISTINCT FROM warehouse.lot_stock_id
        """
    )
    cr.execute(
        """
        UPDATE stock_request AS request_line
           SET source_location_id = request_order.source_location_id
          FROM stock_request_order AS request_order
         WHERE request_order.id = request_line.order_id
           AND request_line.source_location_id
               IS DISTINCT FROM request_order.source_location_id
        """
    )
