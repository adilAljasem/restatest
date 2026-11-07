import hashlib
import secrets

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PosConfig(models.Model):
    _inherit = "pos.config"

    # Branch-level fields for Blend integration
    blend_enabled = fields.Boolean(string="Enable Blend Integration", copy=False)
    blend_branch_id = fields.Char(string="Blend Branch ID", copy=False, index=True)
    blend_brand_id = fields.Char(string="Blend Brand ID", copy=False, index=True)
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
        compute="_compute_blend_urls",
    )
    blend_login_url = fields.Char(
        string="Blend Login URL",
        compute="_compute_blend_urls",
    )
    blend_menu_api_url = fields.Char(
        string="Blend Menu API URL",
        compute="_compute_blend_urls",
    )
    blend_branches_api_url = fields.Char(
        string="Blend Branches API URL",
        compute="_compute_blend_urls",
    )
    blend_orders_api_url = fields.Char(
        string="Blend Orders API URL",
        compute="_compute_blend_urls",
    )
    blend_order_update_api_url = fields.Char(
        string="Blend Order Update API URL",
        compute="_compute_blend_urls",
    )

    blend_login_user_id = fields.Many2one(
        "res.users",
        string="Blend Login User",
        copy=False,
        domain=[("active", "=", True)],
        default=lambda self: self._blend_default_login_user(),
        help="Dedicated service account whose login/email and Odoo password are sent to POST /api/login.",
    )
    blend_login_token_hash = fields.Char(
        string="Blend Login Token Hash",
        copy=False,
    )
    blend_login_token_issued_at = fields.Datetime(
        string="Token Issued At",
        readonly=True,
        copy=False,
    )
    blend_inbound_token = fields.Char(
        string="Inbound Bearer Token",
        copy=False,
        groups="base.group_system",
        help="Optional pre-shared token for legacy integrations.",
    )



    def _blend_default_login_user(self):
        return self.env.ref(
            "bs_pos_delivery_blend.user_blend_service",
            raise_if_not_found=False,
        )

    @api.depends_context("uid")
    def _compute_blend_urls(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "").rstrip("/")
        for config in self:
            config.blend_webhook_url = "%s/api/blend/webhook" % base_url
            config.blend_login_url = "%s/api/login" % base_url
            config.blend_menu_api_url = "%s/api/menu" % base_url
            config.blend_branches_api_url = "%s/api/branches" % base_url
            config.blend_orders_api_url = "%s/api/orders" % base_url
            config.blend_order_update_api_url = "%s/api/order/update" % base_url

    def _blend_issue_login_token(self):
        self.ensure_one()
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        self.sudo().write({
            "blend_login_token_hash": token_hash,
            "blend_login_token_issued_at": fields.Datetime.now(),
        })
        return raw_token

    def action_revoke_blend_login_token(self):
        self.ensure_one()
        self.sudo().write({
            "blend_login_token_hash": False,
            "blend_login_token_issued_at": False,
        })
        return True

    @api.constrains("blend_enabled", "blend_branch_id")
    def _check_blend_branch_id(self):
        for config in self:
            if config.blend_enabled and not (config.blend_branch_id or "").strip():
                raise ValidationError("Blend Branch ID is required when Blend is enabled.")

    def write(self, vals):
        result = super().write(vals)
        if (
            not self.env.context.get("bs_blend_store_sync")
            and {"blend_branch_id", "blend_brand_id"}.intersection(vals)
        ):
            connections = self.env["bs.delivery.connection"].sudo().search(
                [
                    ("provider_id.code", "=", "blend"),
                    ("pos_config_ids", "in", self.ids),
                    ("active", "=", True),
                ]
            )
            connections._blend_sync_stores()
        return result

    def _blend_connection(self, *, connected=False):
        self.ensure_one()
        domain = [
            ("provider_id.code", "=", "blend"),
            ("company_id", "=", self.company_id.id),
            ("pos_config_ids", "in", self.id),
            ("active", "=", True),
        ]
        if connected:
            domain.append(("state", "=", "connected"))
        return self.env["bs.delivery.connection"].sudo().search(domain, limit=1)

    @api.model
    def _load_pos_data_fields(self, config):
        result = super()._load_pos_data_fields(config)
        if not result:
            return result
        result = list(result)
        for field_name in ("blend_enabled", "blend_branch_id", "blend_available"):
            if field_name not in result:
                result.append(field_name)
        return result

    def _blend_notify(self, payload):
        for config in self:
            config._notify("BLEND_AVAILABILITY", payload)
