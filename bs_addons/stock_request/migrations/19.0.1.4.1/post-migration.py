# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).


def migrate(cr, version):
    """Preserve defaults from databases where the former column exists."""
    cr.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = 'res_users'
           AND column_name = 'default_stock_request_warehouse_id'
        """
    )
    if not cr.fetchone():
        return
    cr.execute(
        """
        INSERT INTO ir_default (
            field_id,
            user_id,
            json_value,
            create_uid,
            write_uid,
            create_date,
            write_date
        )
        SELECT field.id,
               users.id,
               users.default_stock_request_warehouse_id::text,
               1,
               1,
               NOW(),
               NOW()
          FROM res_users AS users
          JOIN ir_model_fields AS field
            ON field.model = 'res.users'
           AND field.name = 'default_stock_request_warehouse_id'
         WHERE users.default_stock_request_warehouse_id IS NOT NULL
           AND NOT EXISTS (
               SELECT 1
                 FROM ir_default AS defaults
                WHERE defaults.field_id = field.id
                  AND defaults.user_id = users.id
                  AND defaults.company_id IS NULL
                  AND defaults.condition IS NULL
           )
        """
    )
