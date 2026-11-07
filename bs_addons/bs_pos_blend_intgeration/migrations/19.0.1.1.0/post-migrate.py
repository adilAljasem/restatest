import logging


_logger = logging.getLogger(__name__)


def _table_exists(cr, table_name):
    cr.execute("SELECT to_regclass(%s)", (table_name,))
    return bool(cr.fetchone()[0])


def migrate(cr, version):
    """Move legacy mapping values onto the native Odoo records."""
    if _table_exists(cr, "blend_product_mapping"):
        cr.execute(
            """
            WITH source AS (
                SELECT DISTINCT ON (product_tmpl_id)
                       product_tmpl_id,
                       external_id,
                       active,
                       is_available,
                       schedule_json,
                       image_urls_json
                  FROM blend_product_mapping
                 WHERE product_tmpl_id IS NOT NULL
                 ORDER BY product_tmpl_id, active DESC, id DESC
            )
            UPDATE product_template AS product
               SET blend_external_id = source.external_id,
                   blend_menu_active = source.active,
                   blend_is_available = source.active AND source.is_available,
                   blend_schedule_json = source.schedule_json,
                   blend_image_urls_json = source.image_urls_json
              FROM source
             WHERE product.id = source.product_tmpl_id
            """
        )
        _logger.info("Migrated legacy Blend product IDs to product.template")

    if _table_exists(cr, "blend_category_mapping"):
        cr.execute(
            """
            WITH source AS (
                SELECT DISTINCT ON (pos_category_id)
                       pos_category_id,
                       external_id,
                       active,
                       schedule_json,
                       item_order_json
                  FROM blend_category_mapping
                 WHERE pos_category_id IS NOT NULL
                 ORDER BY pos_category_id, active DESC, id DESC
            )
            UPDATE pos_category AS category
               SET blend_external_id = source.external_id,
                   blend_menu_active = source.active,
                   blend_schedule_json = source.schedule_json,
                   blend_item_order_json = source.item_order_json
              FROM source
             WHERE category.id = source.pos_category_id
            """
        )
        _logger.info("Migrated legacy Blend category IDs to pos.category")

    if _table_exists(cr, "blend_modifier_option_mapping"):
        cr.execute(
            """
            WITH source AS (
                SELECT DISTINCT ON (product_tmpl_id)
                       product_tmpl_id,
                       external_id,
                       active,
                       is_available
                  FROM blend_modifier_option_mapping
                 WHERE product_tmpl_id IS NOT NULL
                 ORDER BY product_tmpl_id, active DESC, id DESC
            )
            UPDATE product_template AS product
               SET blend_external_id = source.external_id,
                   blend_menu_active = source.active,
                   blend_is_available = source.active AND source.is_available
              FROM source
             WHERE product.id = source.product_tmpl_id
            """
        )
        _logger.info("Migrated Blend modifier IDs to product.template")
