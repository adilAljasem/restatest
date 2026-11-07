# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo.tools import SQL


def migrate(cr, version):
    """Remove obsolete settings records without loading the ORM registry."""
    targets = {
        "ir_ui_menu": "menu_stock_request_config",
        "ir_act_window": "action_stock_request_config",
        "ir_ui_view": "res_config_settings_view_form",
        "res_groups": "group_stock_request_order",
    }
    for table, xmlid_name in targets.items():
        cr.execute(
            SQL(
                """
            DELETE FROM %s
             WHERE id IN (
                SELECT res_id
                  FROM ir_model_data
                 WHERE module = 'stock_request'
                   AND name = %s
             )
            """,
                SQL.identifier(table),
                xmlid_name,
            )
        )
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = 'stock_request'
           AND name IN (
               'menu_stock_request_config',
               'action_stock_request_config',
               'res_config_settings_view_form',
               'group_stock_request_order'
           )
        """
    )
