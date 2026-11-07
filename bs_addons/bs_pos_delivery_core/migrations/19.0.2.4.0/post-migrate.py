from odoo import SUPERUSER_ID, api

from odoo.addons.bs_pos_delivery_core.secure_fields import encrypt_legacy_columns


def migrate(cr, version):
    """Repair webhook digests after enabling decrypt-on-record reads.

    Versions 19.0.2.2.0 and 19.0.2.3.0 encrypted legacy columns before
    recomputing the webhook digest.  On Odoo 19 a freshly fetched custom Char
    column bypasses ``convert_to_cache``; consequently those migrations could
    hash the ciphertext instead of the actual opaque webhook token.  Re-run
    the idempotent encryption pass and derive every digest from plaintext.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    encrypt_legacy_columns(
        env,
        "bs_delivery_connection",
        (
            "api_key",
            "api_secret",
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
