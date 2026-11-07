import hashlib
import hmac
import secrets
from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from odoo.addons.bs_pos_delivery_core.exceptions import (
    DeliveryAuthenticationError,
    DeliveryBusinessError,
)


class DeliveryConnection(models.Model):
    _inherit = "bs.delivery.connection"

    blend_login_method = fields.Selection(
        [
            ("bearer", "Bearer Token"),
            ("odoo_user", "Odoo User"),
            ("credentials", "Custom Username & Password"),
        ],
        string="Blend Authentication",
        required=True,
        default="bearer",
        groups="bs_pos_delivery_core.group_delivery_integration_manager",
    )
    blend_login_user_id = fields.Many2one(
        "res.users",
        string="Odoo Login User",
        copy=False,
        domain="[('active', '=', True), ('share', '=', False), ('company_ids', 'in', [company_id])]",
        groups="bs_pos_delivery_core.group_delivery_integration_manager",
        help="Use this user's Odoo login and password. Blend operations are attributed to this user.",
    )
    blend_login_username = fields.Char(
        related="blend_login_user_id.login", string="Odoo Login / Email",
        groups="bs_pos_delivery_core.group_delivery_integration_manager",
    )
    blend_execution_user_id = fields.Many2one(
        "res.users",
        string="Execution User",
        default=lambda self: self._blend_default_execution_user(),
        domain="[('active', '=', True), ('share', '=', False), ('company_ids', 'in', [company_id])]",
        groups="bs_pos_delivery_core.group_delivery_integration_manager",
        help="Real Odoo user responsible for operations authenticated with custom credentials. Requires POS and Delivery Operator access.",
    )
    blend_login_user_signature = fields.Char(
        copy=False, readonly=True,
        groups="bs_pos_delivery_core.group_delivery_integration_manager",
    )

    blend_brand_id = fields.Char(
        string="Default Blend Brand ID",
        copy=False,
        help="Used when a branch does not override the Blend brand identifier.",
    )
    blend_order_url = fields.Char(
        string="Blend Order Created URL",
        copy=False,
    )
    blend_outbound_webhook_url = fields.Char(
        string="Blend Outbound Webhook URL",
        copy=False,
    )
    blend_menu_url = fields.Char(
        string="Blend Get Menu URL",
        copy=False,
    )
    blend_schema = fields.Selection(
        [
            ("primary", "Primary API (order_updated)"),
            ("pos", "POS API (order.updated)"),
        ],
        string="Outgoing Status Schema",
        default="pos",
        required=True,
    )
    blend_timeout_seconds = fields.Integer(
        string="Blend HTTP Timeout (seconds)",
        default=10,
        required=True,
    )
    blend_sync_images = fields.Boolean(
        string="Download Blend Images",
        default=True,
    )
    blend_login_token_hash = fields.Char(
        copy=False,
        readonly=True,
        groups="bs_pos_delivery_core.group_delivery_integration_manager",
    )
    blend_login_token_issued_at = fields.Datetime(
        string="Login Token Issued At",
        copy=False,
        readonly=True,
        groups="bs_pos_delivery_core.group_delivery_integration_manager",
    )
    blend_legacy_webhook_url = fields.Char(
        string="Legacy Blend Webhook URL",
        compute="_compute_blend_legacy_webhook_url",
    )
    blend_login_url = fields.Char(
        string="Blend Login URL", compute="_compute_blend_order_intake_urls",
    )
    blend_branches_api_url = fields.Char(
        string="Blend Branches API URL", compute="_compute_blend_order_intake_urls",
    )
    blend_menu_api_url = fields.Char(
        string="Blend Menu API URL", compute="_compute_blend_order_intake_urls",
    )
    blend_order_intake_url = fields.Char(
        string="Blend Order Intake URL", compute="_compute_blend_order_intake_urls",
    )
    blend_order_status_intake_url = fields.Char(
        string="Blend Order Status Intake URL", compute="_compute_blend_order_intake_urls",
    )

    _blend_timeout_valid = models.Constraint(
        "CHECK(blend_timeout_seconds >= 1 AND blend_timeout_seconds <= 120)",
        "The Blend timeout must be between 1 and 120 seconds.",
    )

    @api.depends_context("uid")
    def _compute_blend_legacy_webhook_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for connection in self:
            connection.blend_legacy_webhook_url = "%s/api/blend/webhook" % base_url.rstrip("/")

    @api.depends_context("uid")
    def _compute_blend_order_intake_urls(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "").rstrip("/")
        for connection in self:
            connection.blend_login_url = "%s/api/login" % base_url
            connection.blend_branches_api_url = "%s/api/branches" % base_url
            connection.blend_menu_api_url = "%s/api/menu" % base_url
            connection.blend_order_intake_url = "%s/api/orders" % base_url
            connection.blend_order_status_intake_url = "%s/api/order/update" % base_url

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for incoming in vals_list:
            values = dict(incoming)
            provider = self.env["bs.delivery.provider"].browse(
                values.get("provider_id") or self.env.context.get("default_provider_id")
            )
            if provider.code == "blend":
                method = values.setdefault(
                    "blend_login_method",
                    "credentials" if values.get("auth_scheme") == "basic" else "bearer",
                )
                if method in ("odoo_user", "credentials"):
                    values["auth_scheme"] = "basic"
                values.setdefault("auth_scheme", "bearer")
                values.setdefault("outgoing_mode", "queued")
                values.setdefault("blend_schema", "pos")
                values.setdefault("blend_timeout_seconds", 10)
            prepared.append(values)
        records = super().create(prepared)
        records.filtered(lambda record: record.provider_id.code == "blend")._blend_sync_stores()
        return records

    def write(self, vals):
        vals = self._apply_secret_placeholder_values(vals, self._SECRET_FIELDS)
        if len(self) > 1 and {"pos_config_ids", "auth_scheme", "blend_login_method"}.intersection(vals):
            for connection in self:
                connection.write(vals)
            return True
        if len(self) == 1 and self.provider_id.code == "blend":
            if "blend_login_method" in vals:
                vals["auth_scheme"] = (
                    "basic" if vals["blend_login_method"] in ("odoo_user", "credentials") else "bearer"
                )
            elif "auth_scheme" in vals:
                vals["blend_login_method"] = "credentials" if vals["auth_scheme"] == "basic" else "bearer"
        if (
            {"username", "password", "auth_scheme", "blend_login_method",
             "blend_login_user_id", "blend_execution_user_id"}.intersection(vals)
            or vals.get("active") is False
            or ("state" in vals and vals["state"] != "connected")
        ):
            vals.update({
                "blend_login_token_hash": False,
                "blend_login_token_issued_at": False,
                "blend_login_user_signature": False,
            })
        if self.env.context.get("bs_delivery_store_scope_sync"):
            return super().write(vals)
        if "pos_config_ids" in vals and len(self) > 1:
            for connection in self:
                connection.write(vals)
            return True
        previous_configs = {
            connection.id: connection.pos_config_ids
            for connection in self.filtered(lambda record: record.provider_id.code == "blend")
        }
        scoped = (
            self.with_context(bs_delivery_store_scope_sync=True)
            if "pos_config_ids" in vals and self.provider_id.code == "blend"
            else self
        )
        result = super(DeliveryConnection, scoped).write(vals)
        blend_connections = self.filtered(lambda record: record.provider_id.code == "blend")
        blend_connections._blend_sync_stores()
        if "pos_config_ids" in vals or "active" in vals or "provider_id" in vals:
            removed = self.env["pos.config"]
            for connection in blend_connections:
                removed |= previous_configs.get(connection.id, self.env["pos.config"]) - connection.pos_config_ids
            for config in removed:
                if not self.search_count(
                    [
                        ("provider_id.code", "=", "blend"),
                        ("pos_config_ids", "in", config.id),
                        ("active", "=", True),
                    ]
                ):
                    config.with_context(bs_blend_store_sync=True).blend_enabled = False
        return result

    @api.model
    def _blend_default_execution_user(self):
        user_id = self.env["ir.config_parameter"].sudo().get_param("bs_pos_delivery_core.api_user_id", "")
        if str(user_id).isdigit():
            user = self.env["res.users"].sudo().browse(int(user_id)).exists()
            if user:
                return user
        return self.env.ref("bs_pos_delivery_core.user_delivery_api", raise_if_not_found=False)

    def _blend_execution_environment(self):
        """Bind authenticated operations to an eligible account and one company.

        Core retains its scoped elevation for integration-managed records;
        the account's identity is preserved for records and workflow audits.
        """
        self.ensure_one()
        connection = self.sudo()
        if connection.blend_login_method == "bearer":
            return self.env
        user = (
            connection.blend_login_user_id
            if connection.blend_login_method == "odoo_user"
            else connection.blend_execution_user_id
        )
        if (
            not user or not user.active or user.share
            or connection.company_id not in user.company_ids
            or not user.has_group("point_of_sale.group_pos_user")
            or not user.has_group("bs_pos_delivery_core.group_delivery_operator")
        ):
            raise DeliveryAuthenticationError(_(
                "Select an active internal Odoo user with POS and Delivery Operator access to this connection's company."
            ))
        scoped = connection.with_user(user).with_context(
            allowed_company_ids=[connection.company_id.id],
        ).with_company(connection.company_id)
        try:
            scoped.check_access("read")
            scoped.pos_config_ids.check_access("read")
        except AccessError as error:
            raise DeliveryAuthenticationError(_("The execution user cannot access this connection's POS branches.")) from error
        return scoped.env

    def _blend_issue_login_token(self):
        self.ensure_one()
        token = secrets.token_urlsafe(32)
        self.sudo().write({
            "blend_login_token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "blend_login_token_issued_at": fields.Datetime.now(),
            "blend_login_user_signature": (
                self.blend_login_user_id.sudo()._compute_session_token(token)
                if self.blend_login_method == "odoo_user" else False
            ),
        })
        return token

    def action_revoke_blend_login_token(self):
        self.write({
            "blend_login_token_hash": False,
            "blend_login_token_issued_at": False,
            "blend_login_user_signature": False,
        })
        return True

    def _blend_matches_inbound_token(self, token):
        self.ensure_one()
        if self.provider_id.code != "blend" or not self.active:
            return False
        if self.blend_login_method != "odoo_user" and self.webhook_secret and hmac.compare_digest(
            self.webhook_secret.encode("utf-8"), token.encode("utf-8")
        ):
            try:
                self._blend_execution_environment()
            except DeliveryAuthenticationError:
                return False
            return True
        valid = bool(
            self.state == "connected"
            and self.blend_login_method in ("odoo_user", "credentials")
            and (self.blend_login_method == "odoo_user" or (self.username and self.password))
            and self.blend_login_token_hash
            and hmac.compare_digest(
                self.blend_login_token_hash,
                hashlib.sha256(token.encode("utf-8")).hexdigest(),
            )
        )
        if not valid:
            return False
        try:
            self._blend_execution_environment()
        except DeliveryAuthenticationError:
            return False
        if self.blend_login_method == "odoo_user":
            return bool(self.blend_login_user_signature) and hmac.compare_digest(
                self.blend_login_user_signature,
                self.blend_login_user_id.sudo()._compute_session_token(token),
            )
        return True

    @api.constrains("provider_id", "pos_config_ids", "active")
    def _check_blend_branch_connection_uniqueness(self):
        for connection in self.filtered(
            lambda record: record.active and record.provider_id.code == "blend"
        ):
            duplicate = self.search(
                [
                    ("id", "!=", connection.id),
                    ("provider_id.code", "=", "blend"),
                    ("company_id", "=", connection.company_id.id),
                    ("pos_config_ids", "in", connection.pos_config_ids.ids),
                    ("active", "=", True),
                ],
                limit=1,
            )
            if duplicate:
                raise ValidationError(
                    _("A POS branch can belong to only one active Blend connection.")
                )

    def _blend_sync_stores(self):
        Store = self.env["bs.delivery.store.binding"].sudo().with_context(
            active_test=False, bs_delivery_defer_store_sync=True
        )
        for connection in self.filtered(lambda record: record.provider_id.code == "blend"):
            linked_stores = Store.search([("connection_id", "=", connection.id)])
            for config in connection.pos_config_ids:
                store = linked_stores.filtered(lambda item: item.pos_config_id == config)[:1]
                branch_id = (config.blend_branch_id or (store and store.remote_store_id) or "").strip()
                if not branch_id:
                    continue
                config.with_context(bs_blend_store_sync=True).write({
                    "blend_branch_id": branch_id,
                    "blend_enabled": True,
                })
                values = {
                    "remote_store_id": branch_id,
                    "remote_vendor_id": (
                        (config.blend_brand_id or connection.blend_brand_id or "").strip()
                        or False
                    ),
                    "active": True,
                }
                if store:
                    store.write(values)
                else:
                    Store.create(
                        {
                            "connection_id": connection.id,
                            "pos_config_id": config.id,
                            **values,
                        }
                    )
            stale = linked_stores.filtered(
                lambda store: store.pos_config_id not in connection.pos_config_ids
            )
            if stale:
                stale.write({"active": False})
            connection._synchronize_pos_scope_from_store_bindings(authoritative=True)
        return True

    def _blend_validate_configuration(self):
        self.ensure_one()
        if self.provider_id.code != "blend":
            raise DeliveryBusinessError(_("This operation requires a Blend connection."))
        if not self.pos_config_ids:
            raise DeliveryBusinessError(_("Link at least one Point of Sale branch."))
        missing = self.pos_config_ids.filtered(lambda config: not config.blend_branch_id)
        if missing:
            raise DeliveryBusinessError(
                _("Every linked Point of Sale branch must have a Blend Branch ID.")
            )
        if self.blend_login_method != "bearer":
            self._blend_execution_environment()
        if self.blend_login_method == "credentials":
            if not (self.username or "").strip() or not self.password:
                raise DeliveryAuthenticationError(
                    _("Configure the Blend HTTP Basic Username and Password for POST /api/login.")
                )
        elif self.blend_login_method == "bearer" and not self.webhook_secret:
            raise DeliveryAuthenticationError(
                _("Configure the inbound Blend Bearer token in Webhook Secret.")
            )
        self._blend_validate_url(
            self.blend_outbound_webhook_url, label=_("Blend Outbound Webhook URL")
        )
        for label, url in (
            (_("Blend Get Menu URL"), self.blend_menu_url),
        ):
            if url:
                self._blend_validate_url(url, label=label)
        return True

    def _blend_validate_url(self, url, *, label):
        self.ensure_one()
        parsed = urlparse(str(url or ""))
        allow_insecure = self.env["ir.config_parameter"].sudo().get_param(
            "blend.allow_insecure_urls"
        ) == "1"
        if not parsed.netloc or (parsed.scheme != "https" and not allow_insecure):
            raise DeliveryBusinessError(_("%s must be a valid HTTPS URL.") % label)
        return str(url).strip()

    def _blend_store_for_config(self, config):
        self.ensure_one()
        return self.env["bs.delivery.store.binding"].sudo().search(
            [
                ("connection_id", "=", self.id),
                ("pos_config_id", "=", config.id),
                ("active", "=", True),
            ],
            limit=1,
        )
