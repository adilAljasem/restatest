"""Bring legacy Blend branch fields in line with active store mappings."""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    connections = env["bs.delivery.connection"].search([
        ("provider_id.code", "=", "blend"),
    ])
    stores = env["bs.delivery.store.binding"].with_context(active_test=False).search([
        ("provider_id.code", "=", "blend"),
    ])
    for store in stores.filtered("active"):
        if not store.remote_vendor_id and store.pos_config_id.blend_brand_id:
            store.with_context(bs_delivery_defer_store_sync=True).write({
                "remote_vendor_id": store.pos_config_id.blend_brand_id,
            })
    connections._synchronize_pos_scope_from_store_bindings(authoritative=True)
    configs = stores.mapped("pos_config_id") | env["pos.config"].search([
        ("blend_enabled", "=", True),
    ])
    stores._sync_blend_branch_fields(configs)
