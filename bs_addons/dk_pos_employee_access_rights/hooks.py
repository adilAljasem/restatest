# -*- coding: utf-8 -*-

def pre_init_hook(env):
    """Migrate module technical name from the legacy Sensible module."""
    env.cr.execute("""
        UPDATE ir_module_module
           SET name = 'dk_pos_employee_access_rights'
         WHERE name = 'sensible_pos_access_rights_employee'
    """)
    env.cr.execute("""
        UPDATE ir_model_data
           SET module = 'dk_pos_employee_access_rights'
         WHERE module = 'sensible_pos_access_rights_employee'
    """)
