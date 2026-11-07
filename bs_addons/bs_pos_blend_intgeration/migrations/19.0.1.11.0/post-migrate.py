import logging

from odoo import SUPERUSER_ID, api


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Assign the provisioned service user to ready, unassigned Blend configs."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    blend_user = env.ref(
        "bs_pos_blend_intgeration.user_blend_service",
        raise_if_not_found=False,
    )
    if not blend_user:
        _logger.warning("Blend service user was not found; no POS configs were linked")
        return

    configs = env["pos.config"].search(
        [
            ("blend_enabled", "=", True),
            ("blend_login_user_id", "=", False),
            ("blend_brand_id", "not in", [False, ""]),
        ]
    )
    if configs:
        configs.write({"blend_login_user_id": blend_user.id})
        _logger.info(
            "Assigned Blend User to %s enabled Blend POS configuration(s)",
            len(configs),
        )
