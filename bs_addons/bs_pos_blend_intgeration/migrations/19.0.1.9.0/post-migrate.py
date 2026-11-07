import logging


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Disable queued calls from the retired Blend-to-POS menu.updated flow."""
    cr.execute(
        """
        UPDATE blend_integration_log
           SET state = 'failed',
               next_retry_at = NULL,
               completed_at = NOW(),
               error_message = %s
         WHERE direction = 'outbound'
           AND event = 'menu_pull'
           AND state IN ('received', 'processing', 'pending', 'retry')
        """,
        ["Retired: menu.updated is POS-to-Blend only; Blend calls Odoo GET /api/menu."],
    )
    if cr.rowcount:
        _logger.info("Disabled %s obsolete Blend menu_pull queue entries", cr.rowcount)
