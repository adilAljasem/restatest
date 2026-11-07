from odoo import SUPERUSER_ID, api

from odoo.addons.bs_pos_delivery_core.secure_fields import encrypt_legacy_columns


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    encrypt_legacy_columns(
        env,
        "bs_delivery_connection",
        (
            "api_secret",
            "api_key",
            "password",
            "access_token",
            "webhook_secret",
            "webhook_token",
        ),
    )
    connections = env["bs.delivery.connection"].with_context(active_test=False).search([])
    for connection in connections:
        digest = connection._webhook_token_digest_value(connection.webhook_token)
        if connection.webhook_token_digest != digest:
            connection.sudo().write({"webhook_token_digest": digest})
