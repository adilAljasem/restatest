# -*- coding: utf-8 -*-


def migrate(cr, version):
    """Move template-wide selector settings before Odoo removes their columns."""
    cr.execute("""
        SELECT column_name
          FROM information_schema.columns
         WHERE table_name = 'product_template'
           AND column_name IN ('apply_selector', 'min_selector', 'max_selector')
    """)
    existing_columns = {column[0] for column in cr.fetchall()}
    required_columns = {'apply_selector', 'min_selector', 'max_selector'}
    if not required_columns.issubset(existing_columns):
        return

    cr.execute("""
        ALTER TABLE product_modifiers
            ADD COLUMN IF NOT EXISTS apply_selector boolean,
            ADD COLUMN IF NOT EXISTS min_selector integer,
            ADD COLUMN IF NOT EXISTS max_selector integer
    """)
    cr.execute("""
        UPDATE product_modifiers AS modifier
           SET apply_selector = template.apply_selector,
               min_selector = template.min_selector,
               max_selector = template.max_selector
          FROM product_template AS template
         WHERE modifier.product_id = template.id
    """)
