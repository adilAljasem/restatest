# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).


def migrate(cr, version):
    """Disable the obsolete Purchase settings view on upgraded databases."""
    cr.execute(
        """
        UPDATE ir_ui_view
           SET active = FALSE
         WHERE id = (
                   SELECT res_id
                     FROM ir_model_data
                    WHERE module = 'stock_request'
                      AND name = 'stock_request_res_config_settings_view_form_purchase'
                      AND model = 'ir.ui.view'
                    LIMIT 1
               )
        """
    )
