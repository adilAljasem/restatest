# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Link existing two-step moves to their request lines and recompute progress."""
    cr.execute(
        """
        UPDATE stock_move AS move
           SET stock_request_line_id = links.stock_request_id
          FROM (
                SELECT stock_move_id, MIN(stock_request_id) AS stock_request_id
                  FROM stock_request_allocation
              GROUP BY stock_move_id
                HAVING COUNT(DISTINCT stock_request_id) = 1
               ) AS links
         WHERE move.id = links.stock_move_id
           AND move.stock_request_line_id IS NULL
        """
    )
    cr.execute(
        """
        UPDATE stock_move AS origin
           SET stock_request_line_id = links.stock_request_line_id
          FROM (
                SELECT relation.move_orig_id,
                       MIN(destination.stock_request_line_id) AS stock_request_line_id
                  FROM stock_move_move_rel AS relation
                  JOIN stock_move AS destination
                    ON destination.id = relation.move_dest_id
                 WHERE destination.stock_request_line_id IS NOT NULL
              GROUP BY relation.move_orig_id
                HAVING COUNT(DISTINCT destination.stock_request_line_id) = 1
               ) AS links
         WHERE origin.id = links.move_orig_id
           AND origin.stock_request_line_id IS NULL
        """
    )
    cr.execute(
        """
        UPDATE stock_move AS move
           SET stock_request_line_id = matches.stock_request_line_id
          FROM (
                SELECT move.id AS move_id,
                       MIN(request.id) AS stock_request_line_id
                  FROM stock_move AS move
                  JOIN stock_picking AS picking
                    ON picking.id = move.picking_id
                  JOIN stock_request AS request
                    ON request.order_id = picking.stock_request_order_id
                   AND request.product_id = move.product_id
                 WHERE move.stock_request_line_id IS NULL
                   AND picking.stock_request_order_id IS NOT NULL
              GROUP BY move.id
                HAVING COUNT(request.id) = 1
               ) AS matches
         WHERE move.id = matches.move_id
        """
    )

    env = api.Environment(cr, SUPERUSER_ID, {})
    requests = env["stock.request"].search([("order_id", "!=", False)])
    requests._compute_transfer_quantities()
    orders = requests.mapped("order_id")
    orders._compute_pending_operations()
    orders._compute_operation_picking_counts()
    orders._sync_workflow_state_from_pickings()
