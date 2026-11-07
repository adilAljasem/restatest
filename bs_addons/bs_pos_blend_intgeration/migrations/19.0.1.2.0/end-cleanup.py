import logging


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Remove obsolete mapping tables after Odoo removes their metadata."""
    cr.execute("DROP TABLE IF EXISTS blend_product_category_rel")
    cr.execute("DROP TABLE IF EXISTS blend_product_mapping")
    cr.execute("DROP TABLE IF EXISTS blend_category_mapping")
    _logger.info("Removed legacy Blend product and category mapping tables")
