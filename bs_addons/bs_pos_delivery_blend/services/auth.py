import hashlib
import hmac

from odoo.exceptions import AccessDenied

from odoo.addons.bs_pos_delivery_core.exceptions import DeliveryAuthenticationError

from .exceptions import BlendAPIError, BlendAuthenticationError


class BlendAuthService:
    """Authenticate Blend requests via bearer token (login token, inbound token, or connection secret)."""

    def __init__(self, env):
        self.env = env

    def login_connection(self, login, password, user_agent_env=None):
        """Exchange the selected connection login method for a bearer token.

        Return False when no connection uses this login so the controller can
        retain the existing Odoo-user authentication flow.
        """
        connections = self.env["bs.delivery.connection"].sudo().search([
            ("provider_id.code", "=", "blend"),
            ("active", "=", True),
            "|",
            "&", ("blend_login_method", "=", "credentials"), ("username", "=", login),
            "&", ("blend_login_method", "=", "odoo_user"), ("blend_login_user_id.login", "=", login),
        ])
        if not connections:
            return False
        custom = connections.filtered(lambda conn: conn.blend_login_method == "credentials")
        if custom:
            with self.env["res.users"]._assert_can_auth(user=login):
                matched = custom.filtered(
                    lambda conn: conn.password and hmac.compare_digest(
                        conn.password.encode("utf-8"), password.encode("utf-8")
                    )
                )
                return self._issue_connection_token(matched)
        auth_info = self.env["res.users"].authenticate(
            {"type": "password", "login": login, "password": password},
            user_agent_env or {"interactive": False},
        )
        return self._issue_connection_token(connections.filtered(
            lambda conn: conn.blend_login_user_id.id == auth_info["uid"]
        ))

    def _issue_connection_token(self, connections):
        matched = connections.filtered(
            lambda conn: conn.state == "connected" and conn.pos_config_ids.filtered("blend_enabled")
        )
        # The login payload cannot select a company or connection. Never
        # broaden a token's scope when credentials have been reused.
        if len(matched) != 1:
            raise AccessDenied()
        try:
            matched._blend_execution_environment()
        except DeliveryAuthenticationError as error:
            # Credentials have already passed. Report the execution-account
            # configuration separately from an incorrect login/password.
            raise BlendAPIError(str(error), 403) from error
        return matched._blend_issue_login_token()

    def legacy_login_configs(self, user_id):
        """Keep POS-level logins only where no connection login method replaces them."""
        configs = self.env["pos.config"].sudo().search([
            ("blend_enabled", "=", True), ("blend_login_user_id", "=", user_id),
        ])
        connections = self.env["bs.delivery.connection"].sudo().search([
            ("provider_id.code", "=", "blend"), ("active", "=", True),
            ("blend_login_method", "!=", "bearer"), ("pos_config_ids", "in", configs.ids),
        ])
        return configs - connections.pos_config_ids

    @staticmethod
    def bearer_token(authorization):
        scheme, separator, token = str(authorization or "").partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            raise BlendAuthenticationError()
        return token.strip()

    def authorized_configs(self, authorization):
        token = self.bearer_token(authorization)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()

        # Check POS configurations
        configs = self.env["pos.config"].sudo().search([("blend_enabled", "=", True)])
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

        # Also check Delivery Connections for Blend
        connections = self.env["bs.delivery.connection"].sudo().search([
            ("provider_id.code", "=", "blend"),
            ("active", "=", True),
        ])
        authorized -= connections.filtered(
            lambda conn: conn.blend_login_method != "bearer"
        ).pos_config_ids
        connection_configs = self.env["pos.config"].browse()
        matched_connection = False
        for conn in connections:
            if conn._blend_matches_inbound_token(token):
                matched_connection = True
                connection_configs |= conn.pos_config_ids

        if not authorized and not matched_connection:
            raise BlendAuthenticationError()
        return authorized | connection_configs

    def execution_environment(self, configs, authorization):
        token = self.bearer_token(authorization)
        connections = self.env["bs.delivery.connection"].sudo().search([
            ("provider_id.code", "=", "blend"), ("active", "=", True),
            ("blend_login_method", "!=", "bearer"), ("pos_config_ids", "in", configs.ids),
        ]).filtered(lambda conn: conn._blend_matches_inbound_token(token))
        if len(connections) > 1:
            raise BlendAPIError("Blend token must resolve to one execution user and connection.", 409)
        return connections._blend_execution_environment() if connections else self.env

    def authorized_menu_scope(self, authorization):
        """Resolve one connection for a response with one price per product."""
        configs = self.authorized_configs(authorization)
        token = self.bearer_token(authorization)
        connections = self.env["bs.delivery.connection"].sudo().search([
            ("provider_id.code", "=", "blend"),
            ("active", "=", True),
        ])
        matched = connections.filtered(lambda conn: conn._blend_matches_inbound_token(token))
        if not matched:
            matched = connections.filtered(lambda conn: bool(conn.pos_config_ids & configs))
        if len(matched) != 1:
            raise BlendAPIError("Blend menu token must resolve to one delivery connection.", 409)
        connection = matched[0]
        scoped_configs = configs & connection.pos_config_ids
        if not scoped_configs:
            raise BlendAPIError("No authorized Blend POS branches are mapped to this connection.", 409)
        return scoped_configs, connection

    def authorized_stores(self, stores, authorization):
        token = self.bearer_token(authorization)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()

        authorized = self.env["bs.delivery.store.binding"].browse()
        for store in stores:
            config = store.pos_config_id
            conn = store.connection_id
            is_valid = False
            if conn and conn._blend_matches_inbound_token(token):
                is_valid = True
            elif conn and conn.blend_login_method != "bearer":
                continue
            elif config and config.blend_inbound_token and hmac.compare_digest(config.blend_inbound_token, token):
                is_valid = True
            elif (
                config
                and config.blend_login_token_hash
                and config.blend_login_user_id
                and config.blend_login_user_id.active
                and hmac.compare_digest(config.blend_login_token_hash, digest)
            ):
                is_valid = True

            if is_valid:
                authorized |= store

        if not authorized:
            raise BlendAuthenticationError()
        return authorized

