def migrate(cr, version):
    """Retire the former routing addon after its code is merged here."""
    cr.execute(
        """
        UPDATE ir_module_module
           SET state = 'uninstallable'
         WHERE name = 'bs_pos_modifier_preparation_routing'
           AND state != 'uninstallable'
        """
    )
