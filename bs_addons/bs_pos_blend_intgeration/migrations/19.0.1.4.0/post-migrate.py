import logging


_logger = logging.getLogger(__name__)


def _table_exists(cr, table_name):
    cr.execute("SELECT to_regclass(%s)", (table_name,))
    return bool(cr.fetchone()[0])


def migrate(cr, version):
    """Move legacy Blend modifier mappings onto product_modifiers records."""
    if _table_exists(cr, "blend_modifier_group_mapping"):
        cr.execute(
            """
            WITH source AS (
                SELECT DISTINCT ON (modifier_group_id)
                       modifier_group_id,
                       external_id,
                       is_required,
                       min_selection,
                       max_selection,
                       max_per_quantity,
                       item_external_ids_json,
                       active
                  FROM blend_modifier_group_mapping
                 WHERE modifier_group_id IS NOT NULL
                 ORDER BY modifier_group_id, active DESC, id DESC
            )
            UPDATE modifiers_groups AS modifier_group
               SET blend_external_id = source.external_id,
                   blend_is_required = source.is_required,
                   blend_min_selection = source.min_selection,
                   blend_max_selection = source.max_selection,
                   blend_max_per_quantity = source.max_per_quantity,
                   blend_item_external_ids_json = source.item_external_ids_json,
                   blend_menu_active = source.active
              FROM source
             WHERE modifier_group.id = source.modifier_group_id
            """
        )
        _logger.info("Migrated legacy Blend modifier-group mappings to modifiers.groups")

    if _table_exists(cr, "blend_modifier_option_mapping"):
        cr.execute(
            """
            WITH source AS (
                SELECT DISTINCT ON (product_tmpl_id)
                       product_tmpl_id,
                       external_id,
                       price,
                       is_available,
                       active
                  FROM blend_modifier_option_mapping
                 WHERE product_tmpl_id IS NOT NULL
                 ORDER BY product_tmpl_id, active DESC, id DESC
            )
            UPDATE product_template AS product
               SET blend_external_id = source.external_id,
                   list_price = source.price,
                   blend_is_available = source.active AND source.is_available,
                   blend_menu_active = source.active,
                   is_modifier = TRUE,
                   use_as_modifier = TRUE
              FROM source
             WHERE product.id = source.product_tmpl_id
            """
        )
        _logger.info("Migrated legacy Blend modifier-option mappings to product.template")
