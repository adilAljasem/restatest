def migrate(cr, version):
    """Retire the former modifiers-group chatter addon after merging it."""
    cr.execute(
        """
        UPDATE ir_ui_view
           SET active = FALSE
         WHERE id IN (
               SELECT res_id
                 FROM ir_model_data
                WHERE module = 'modifiers_group_chatter_tracking'
                  AND name = 'view_modifiers_groups_form_inherit_chatter_tracking'
                  AND model = 'ir.ui.view'
         )
        """
    )
    cr.execute(
        """
        UPDATE ir_module_module
           SET state = 'uninstallable'
         WHERE name = 'modifiers_group_chatter_tracking'
           AND state != 'uninstallable'
        """
    )
