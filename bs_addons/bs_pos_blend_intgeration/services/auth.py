import hashlib
import hmac

from .exceptions import BlendAuthenticationError


class BlendAuthService:
    """Authenticate Blend requests without creating an Odoo HTTP session."""

    def __init__(self, env):
        self.env = env

    @staticmethod
    def bearer_token(authorization):
        scheme, separator, token = str(authorization or "").partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            raise BlendAuthenticationError()
        return token.strip()

    def authorized_configs(self, authorization):
        token = self.bearer_token(authorization)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        configs = self.env["pos.config"].sudo().search(
            [
                ("blend_enabled", "=", True),
                "|",
                ("blend_login_token_hash", "!=", False),
                ("blend_inbound_token", "!=", False),
            ]
        )
        authorized = configs.filtered(
            lambda config: (
                bool(config.blend_login_token_hash)
                and bool(config.blend_login_user_id)
                and config.blend_login_user_id.active
                and hmac.compare_digest(config.blend_login_token_hash, digest)
            )
            or (
                bool(config.blend_inbound_token)
                and hmac.compare_digest(config.blend_inbound_token, token)
            )
        )
        if not authorized:
            raise BlendAuthenticationError()
        return authorized
