import logging

from odoo import SUPERUSER_ID, api


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Restore the product_modifiers module's field semantics.

    ``is_modifier`` marks a sellable parent product that opens the modifier
    selector. ``use_as_modifier`` marks a product that is an option. Older
    Blend versions set both flags on synchronized options.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    products = env["product.template"].sudo().with_context(active_test=False).search(
        [
            ("blend_config_id", "!=", False),
            ("blend_external_id", "!=", False),
        ]
    )
    options = products.filtered("use_as_modifier")
    parents = products - options
    parents_with_groups = parents.filtered("modifier_grp_ids")
    parents_without_groups = parents - parents_with_groups

    context = {"blend_inbound_sync": True, "tracking_disable": True}
    if options:
        options.with_context(**context).write({"is_modifier": False})
    if parents_with_groups:
        parents_with_groups.with_context(**context).write({"is_modifier": True})
    if parents_without_groups:
        parents_without_groups.with_context(**context).write({"is_modifier": False})

    _logger.info(
        "Normalized Blend modifier semantics for %s option(s) and %s parent product(s)",
        len(options),
        len(parents),
    )
