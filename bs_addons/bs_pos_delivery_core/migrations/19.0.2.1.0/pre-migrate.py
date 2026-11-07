"""Remove connection-wide mapping constraints superseded by store-aware indexes."""

from odoo.tools import sql


_LEGACY_CONSTRAINTS = (
    ("bs_delivery_product_binding", "bs_delivery_product_binding_external_product_unique"),
    ("bs_delivery_category_binding", "bs_delivery_category_binding_external_category_unique"),
    ("bs_delivery_modifier_binding", "bs_delivery_modifier_binding_external_modifier_unique"),
    ("bs_delivery_payment_binding", "bs_delivery_payment_binding_external_payment_unique"),
)


def migrate(cr, version):
    """Drop obsolete UNIQUE constraints before the partial indexes are created."""
    for table_name, constraint_name in _LEGACY_CONSTRAINTS:
        if sql.constraint_definition(cr, table_name, constraint_name):
            sql.drop_constraint(cr, table_name, constraint_name)
