import hashlib
import hmac
import secrets
from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from ..services.order_types import PRESET_FIELDS, preset_field_for_order_type


class PosConfig(models.Model):
    _inherit = "pos.config"

    def _blend_default_login_user(self):
        return self.env.ref(
            "bs_pos_blend_intgeration.user_blend_service",
            raise_if_not_found=False,
        )

    blend_enabled = fields.Boolean(string="Enable Blend Integration", copy=False)
    blend_default_preset_id = fields.Many2one(
        "pos.preset", string="Blend Delivery Preset", ondelete="restrict",
        help="POS preset for incoming Blend orders with order_type delivery. The existing default mapping is retained for delivery only.",
    )
    blend_pickup_preset_id = fields.Many2one(
        "pos.preset", string="Blend Pickup Preset", ondelete="restrict",
        help="Select Take Away for incoming Blend orders with order_type pickup.",
    )
    blend_dinein_preset_id = fields.Many2one(
        "pos.preset", string="Blend Dine-in Preset", ondelete="restrict",
        help="Select Dine In for incoming Blend orders with order_type dinein.",
    )

    def _blend_order_preset(self, order_type):
        self.ensure_one()
        field_name = preset_field_for_order_type(order_type)
        return self[field_name] if field_name else self.env["pos.preset"]

    def _blend_order_preset_error(self, order_type):
        self.ensure_one()
        if not preset_field_for_order_type(order_type):
            return _("Unsupported Blend order type: %s. Expected delivery, pickup or dinein.", order_type)
        preset = self._blend_order_preset(order_type)
        if not self.use_presets or not preset:
            return _("Enable POS presets and configure the Blend preset for order type '%s'.", order_type)
        if preset not in self.available_preset_ids or preset.is_return:
            return _("The Blend preset for '%s' must be an available sale preset on this POS.", order_type)
        if any(company and company != self.company_id for company in (
            preset.pricelist_id.company_id, preset.fiscal_position_id.company_id,
        )):
            return _("The Blend preset's pricelist and fiscal position must belong to this POS company or be shared.")
        return False

    @api.constrains("blend_default_preset_id", "blend_pickup_preset_id", "blend_dinein_preset_id",
                    "available_preset_ids", "use_presets", "company_id")
    def _check_blend_default_preset(self):
        for config in self:
            for order_type, field_name in PRESET_FIELDS.items():
                if config[field_name] and (error := config._blend_order_preset_error(order_type)):
                    raise ValidationError(error)

    blend_auto_accept = fields.Boolean(
        string="Auto Accept Blend Orders",
        default=False,
        copy=False,
        groups="base.group_system",
        help=(
            "Accept new PENDING orders after successful creation and queue an "
            "ACCEPTED update to Blend. Payment/invoicing additionally requires "
            "Pay and Invoice on Blend Acceptance, for newly received orders. "
            "Requires the unified outbound webhook, or the legacy POS status "
            "schema with an HTTPS Order Status URL."
        ),
    )
    blend_branch_id = fields.Char(string="Blend Branch ID", copy=False, index=True)
    blend_brand_id = fields.Char(string="Blend Brand ID", copy=False, index=True)
    blend_inbound_token = fields.Char(
        string="Inbound Bearer Token",
        copy=False,
        groups="base.group_system",
        help=(
            "Optional pre-shared token for legacy integrations. For the production "
            "/api/login flow, select a Blend Login User instead."
        ),
    )
    blend_login_user_id = fields.Many2one(
        "res.users",
        string="Blend Login User",
        copy=False,
        groups="base.group_system",
        domain=[("active", "=", True), ("share", "=", True)],
        help=(
            "Dedicated service account whose login/email and Odoo password are sent "
            "to POST /api/login. The password remains hashed by Odoo and is never "
            "stored on the POS configuration."
        ),
    )
    blend_login_token_hash = fields.Char(
        string="Blend Login Token Hash",
        copy=False,
        readonly=True,
        groups="base.group_system",
    )
    blend_login_token_issued_at = fields.Datetime(
        string="Last Login Token Issued At",
        copy=False,
        readonly=True,
        groups="base.group_system",
    )
    blend_outbound_token = fields.Char(
        string="Outbound Bearer Token",
        copy=False,
        groups="base.group_system",
    )
    blend_outbound_webhook_url = fields.Char(
        string="Blend Outbound Webhook URL", copy=False,
        help="Unified HTTPS destination for menu.updated, item.updated, store.updated and order.updated. Does not replace order_created.",
    )
    blend_legacy_order_creation_enabled = fields.Boolean(
        string="Enable Legacy Order Creation",
        default=False,
        copy=False,
        groups="base.group_system",
        help="Opt in to the separate order_created contract. When disabled, no new order_created jobs are queued and existing waiting jobs cannot be sent. The stored URL is preserved.",
    )
    blend_order_url = fields.Char(
        string="Blend Order Created URL",
        copy=False,
        help="Secure URL supplied by Blend for order_created events.",
    )
    blend_status_url = fields.Char(
        string="Blend Order Status URL",
        copy=False,
        help="Secure URL supplied by Blend for order_updated events.",
    )
    blend_event_url = fields.Char(
        string="Blend Menu Event URL",
        copy=False,
        help="Secure URL supplied by Blend for POS-to-Blend menu.updated events.",
    )
    blend_schema = fields.Selection(
        [
            ("primary", "Primary API (order_updated)"),
            ("pos", "POS API (order.updated)"),
        ],
        string="Outgoing Status Schema",
        default="primary",
        required=True,
    )
    blend_timeout = fields.Integer(
        string="Request Timeout (seconds)",
        default=10,
        required=True,
    )
    blend_retry_max = fields.Integer(
        string="Maximum Attempts",
        default=5,
        required=True,
    )
    blend_sync_images = fields.Boolean(
        string="Download Blend Images",
        default=True,
    )
    blend_available = fields.Boolean(
        string="Blend Branch Available",
        default=True,
        copy=False,
        readonly=True,
    )
    blend_schedule_json = fields.Json(
        string="Blend Branch Schedule",
        copy=False,
        readonly=True,
    )
    blend_last_menu_sync_at = fields.Datetime(
        string="Last Blend Menu Sync",
        copy=False,
        readonly=True,
    )
    blend_webhook_url = fields.Char(
        string="Inbound Webhook URL",
        compute="_compute_blend_public_urls",
    )
    blend_login_url = fields.Char(
        string="Login URL",
        compute="_compute_blend_public_urls",
    )
    blend_menu_api_url = fields.Char(
        string="Get Menu API URL",
        compute="_compute_blend_public_urls",
    )
    blend_branches_api_url = fields.Char(
        string="Get Branches API URL",
        compute="_compute_blend_public_urls",
    )
    blend_orders_api_url = fields.Char(
        string="Create Order API URL",
        compute="_compute_blend_public_urls",
    )
    blend_order_update_api_url = fields.Char(
        string="Update Order API URL",
        compute="_compute_blend_public_urls",
    )
    blend_timezone = fields.Char(
        string="Blend Branch Timezone",
        compute="_compute_blend_timezone",
    )

    _blend_branch_company_unique = models.Constraint(
        "unique (company_id, blend_branch_id)",
        "The Blend Branch ID must be unique inside a company.",
    )

    @api.onchange("blend_enabled", "blend_brand_id")
    def _onchange_blend_default_login_user(self):
        for config in self:
            if config.blend_enabled and not config.blend_login_user_id:
                config.blend_login_user_id = config._blend_default_login_user()

    @api.depends_context("uid")
    def _compute_blend_public_urls(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for config in self:
            root = base_url.rstrip("/")
            config.blend_webhook_url = "%s/api/blend/webhook" % root
            config.blend_login_url = "%s/api/login" % root
            config.blend_menu_api_url = "%s/api/menu" % root
            config.blend_branches_api_url = "%s/api/branches" % root
            config.blend_orders_api_url = "%s/api/orders" % root
            config.blend_order_update_api_url = "%s/api/order/update" % root

    @api.depends("company_id.partner_id.tz")
    def _compute_blend_timezone(self):
        for config in self:
            config.blend_timezone = config.company_id.partner_id.tz or "UTC"

    @api.constrains(
        "blend_enabled",
        "blend_branch_id",
        "blend_inbound_token",
        "blend_login_user_id",
        "blend_brand_id",
        "blend_timeout",
        "blend_retry_max",
    )
    def _check_blend_settings(self):
        for config in self:
            if config.blend_enabled and not (config.blend_branch_id or "").strip():
                raise ValidationError("Blend Branch ID is required when Blend is enabled.")
            if (
                config.blend_enabled
                and not config.blend_login_user_id
                and not (config.blend_inbound_token or "").strip()
            ):
                raise ValidationError(
                    "Configure a Blend Login User or an Inbound Bearer Token "
                    "before enabling Blend."
                )
            if config.blend_login_user_id:
                if not config.blend_login_user_id.share:
                    raise ValidationError(
                        "Blend Login User must be a dedicated portal/service user, "
                        "not an internal Odoo user."
                    )
                if not (config.blend_brand_id or "").strip():
                    raise ValidationError(
                        "Blend Brand ID is required when a Blend Login User is configured."
                    )
                other_brands = self.sudo().search(
                    [
                        ("id", "!=", config.id),
                        ("blend_enabled", "=", True),
                        ("blend_login_user_id", "=", config.blend_login_user_id.id),
                        ("blend_brand_id", "!=", config.blend_brand_id),
                    ],
                    limit=1,
                )
                if other_brands:
                    raise ValidationError(
                        "A Blend Login User can only be assigned to branches of one brand."
                    )
            if not 1 <= config.blend_timeout <= 120:
                raise ValidationError("Blend timeout must be between 1 and 120 seconds.")
            if not 1 <= config.blend_retry_max <= 10:
                raise ValidationError("Blend maximum attempts must be between 1 and 10.")

    @api.constrains(
        "blend_auto_accept", "blend_enabled", "blend_schema",
        "blend_status_url", "blend_brand_id", "blend_outbound_webhook_url",
    )
    def _check_blend_auto_accept_settings(self):
        for config in self:
            if not (config.blend_enabled and config.blend_auto_accept):
                continue
            config._blend_check_decision_settings()

    def _blend_check_decision_settings(self):
        """Validate manual and automatic decisions without reading auth secrets."""
        for config in self:
            if not config.blend_enabled:
                raise ValidationError(_("Blend integration is disabled for this POS."))
            if not config.blend_outbound_webhook_url and config.blend_schema != "pos":
                raise ValidationError(_(
                    "Accepting or rejecting Blend orders requires Outgoing Status Schema "
                    "'POS API (order.updated)'."
                ))
            if not (config.blend_brand_id or "").strip():
                raise ValidationError(_("Blend Brand ID is required to accept or reject orders."))
            try:
                endpoint = config.blend_outbound_webhook_url or config.blend_status_url or ""
                url = urlparse(endpoint)
                valid_url = (
                    url.scheme == "https" and bool(url.hostname)
                    and not url.username and not url.password and not url.fragment
                    and not any(char.isspace() for char in endpoint)
                )
            except ValueError:
                valid_url = False
            if not valid_url:
                raise ValidationError(_(
                    "Configure a valid HTTPS Blend Order Status URL before accepting "
                    "or rejecting orders. Use the webhook URL supplied by Blend, "
                    "not the dashboard login URL."
                ))

    @api.constrains("blend_outbound_webhook_url", "blend_brand_id")
    def _check_blend_outbound_webhook_url(self):
        for config in self:
            endpoint = config.blend_outbound_webhook_url
            if not endpoint:
                continue
            if not (config.blend_brand_id or "").strip():
                raise ValidationError(_("Blend Brand ID is required for the unified webhook."))
            try:
                url = urlparse(endpoint)
                valid = (url.scheme == "https" and url.hostname and url.path.strip("/")
                         and not url.username and not url.password and not url.fragment
                         and not any(char.isspace() for char in endpoint))
            except ValueError:
                valid = False
            if not valid:
                raise ValidationError(_("Use the full HTTPS Blend webhook URL, including its path, not a dashboard URL."))

    def _blend_legacy_order_creation_active(self):
        self.ensure_one()
        # POS operators must respect this administrator-controlled switch
        # without gaining read/write access to integration settings.
        return bool(self.sudo().blend_legacy_order_creation_enabled)

    def _blend_effective_available(self):
        self.ensure_one()
        return bool(self.blend_available and self.env["product.template"]._blend_schedule_visible(
            self.blend_schedule_json, self,
        ))

    def _blend_issue_login_token(self):
        """Issue one opaque token for all enabled branches mapped to a login user.

        Only the SHA-256 digest is stored. The token contains 384 random bits, so a
        fast digest is appropriate and keeps per-webhook validation inexpensive.
        """
        configs = self.sudo().filtered(
            lambda config: config.blend_enabled and config.blend_login_user_id
        )
        if not configs:
            raise ValidationError("No enabled Blend branch is assigned to this login user.")
        token = secrets.token_urlsafe(48)
        configs.write(
            {
                "blend_login_token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
                "blend_login_token_issued_at": fields.Datetime.now(),
            }
        )
        return token

    def write(self, vals):
        from ..services.stock_availability import BlendStockAvailabilityService

        notify = not self.env.context.get("blend_inbound_sync") and bool(
            {"blend_available", "blend_schedule_json", "picking_type_id"}.intersection(vals)
        )
        configs = self.filtered(lambda config: config.blend_enabled and config.blend_outbound_webhook_url) if notify else self.browse()
        before = {config.id: config._blend_effective_available() for config in configs}
        service = BlendStockAvailabilityService(self.env)
        snapshots = []
        for config in configs:
            products = self.env["product.template"]._blend_menu_products(config)
            snapshots.extend(service.capture_products(products | products.modifier_grp_ids.modifier_ids, configs=config))
        values = dict(vals)
        if "blend_login_user_id" in values or values.get("blend_enabled") is False:
            values.update(
                {
                    "blend_login_token_hash": False,
                    "blend_login_token_issued_at": False,
                }
            )
        result = super().write(values)
        service.enqueue(snapshots)
        for config in configs:
            if before[config.id] != config._blend_effective_available():
                service.enqueue_branch(config, before[config.id])
        return result

    def _blend_check_inbound_token(self, token):
        self.ensure_one()
        candidate = str(token or "")
        if not candidate:
            return False
        if self.blend_login_token_hash:
            digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
            if hmac.compare_digest(self.blend_login_token_hash, digest):
                return True
        legacy_token = self.blend_inbound_token or ""
        return bool(legacy_token) and hmac.compare_digest(legacy_token, candidate)

    def action_revoke_blend_login_token(self):
        # Public RPC action: hiding the button is not an authorization check.
        # Validate the entire batch before changing any branch credentials.
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only system administrators may revoke Blend login tokens."))
        self.check_access("write")
        if any(config.company_id not in self.env.companies for config in self):
            raise AccessError(_("You may only revoke Blend login tokens for branches in your selected companies."))
        # Native POS write hooks read session/self-order configuration too.
        # Preserve their existing execution context only after authorizing the
        # caller and exact records; no caller-supplied field values are accepted.
        self.sudo().write(
            {
                "blend_login_token_hash": False,
                "blend_login_token_issued_at": False,
            }
        )
        return True

    @api.model
    def _load_pos_data_fields(self, config):
        result = super()._load_pos_data_fields(config)
        # In Odoo's POS loader an empty list means "read every field".  Keep
        # that sentinel unchanged; replacing it with only the Blend fields
        # removes core fields such as use_pricelist from the POS payload.
        if not result:
            return result
        result = list(result)
        result.extend(
            field_name
            for field_name in (
                "blend_enabled",
                "blend_branch_id",
                "blend_available",
                "blend_schedule_json",
                "blend_timezone",
            )
            if field_name not in result
        )
        return result

    def _blend_notify(self, payload):
        for config in self:
            config._notify("BLEND_AVAILABILITY", payload)
