# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

def migrate(cr, version):
    cr.execute(
        """
        UPDATE ir_act_window
        SET path = NULL
        WHERE id IN (
            SELECT res_id
            FROM ir_model_data
            WHERE module = 'stock_request'
              AND name IN (
                  'action_stock_request_form',
                  'stock_request_order_action'
              )
        )
        """
    )
