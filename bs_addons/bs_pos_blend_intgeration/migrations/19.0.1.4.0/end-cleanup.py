import logging


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Remove obsolete Blend modifier mapping tables after metadata cleanup."""
    cr.execute("DROP TABLE IF EXISTS blend_modifier_option_mapping")
    cr.execute("DROP TABLE IF EXISTS blend_modifier_group_mapping")
    _logger.info("Removed legacy Blend modifier mapping tables")
