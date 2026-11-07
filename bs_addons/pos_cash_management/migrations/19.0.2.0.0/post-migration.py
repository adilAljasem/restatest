# -*- coding: utf-8 -*-
"""Drop the removed "Enforce Maximum Difference on Server" feature.

Odoo deletes the ``ir.model.fields`` records of fields that no longer exist,
but leaves their columns behind. This removes them for good.
"""


def migrate(cr, version):
    cr.execute("ALTER TABLE pos_config DROP COLUMN IF EXISTS pcm_enforce_max_diff")
    cr.execute("""
        DELETE FROM ir_model_fields
         WHERE model = 'pos.config' AND name = 'pcm_enforce_max_diff'
    """)
    cr.execute("""
        DELETE FROM ir_model_fields
         WHERE model = 'res.config.settings' AND name = 'pos_pcm_enforce_max_diff'
    """)
