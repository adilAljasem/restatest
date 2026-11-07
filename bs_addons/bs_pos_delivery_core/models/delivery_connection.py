import hashlib
import logging
import secrets

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..exceptions import DeliveryIntegrationError, DeliveryUnsupportedOperation
from ..secure_fields import EncryptedChar


_logger = logging.getLogger(__name__)


class DeliveryConnection(models.Model):
    """Company-scoped provider account shared by one or more POS branches."""

    _name = "bs.delivery.connection"
    _description = "POS Delivery Connection"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "company_id, provider_id, name, id"
    _check_company_auto = True

    _SECRET_FIELDS = frozenset(
        {
            "api_key",
            "api_secret",
            "password",
            "access_token",
            "webhook_secret",
            "webhook_token",
        }
    )

    name = fields.Char(required=True, tracking=True)
    provider_id = fields.Many2one(
        "bs.delivery.provider",
        required=True,
        index=True,
        ondelete="restrict",
        domain=[("module_available", "=", True)],
    )
    adapter_model_name = fields.Char(related="provider_id.adapter_model_name", readonly=True)
    provider_implementation_state = fields.Selection(
        related="provider_id.implementation_state",
        readonly=True,
    )
    provider_image_128 = fields.Image(
        related="provider_id.image_128",
        string="Platform Logo",
        readonly=True,
    )
    can_pull_menu = fields.Boolean(compute="_compute_menu_capabilities")
    can_push_menu = fields.Boolean(compute="_compute_menu_capabilities")
    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )
    menu_pricelist_id = fields.Many2one(
        "product.pricelist",
        string="Menu Pricelist",
        check_company=True,
        help="Prices sent in this connection's aggregator menu. Order amounts remain unchanged.",
    )
    pos_config_ids = fields.Many2many(
        "pos.config",
        "bs_delivery_connection_pos_config_rel",
        "connection_id",
        "pos_config_id",
        string="Point of Sale Branches",
        check_company=True,
    )
    environment = fields.Selection(
        [("test", "Test"), ("production", "Production")],
        required=True,
        default="test",
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("connected", "Connected"),
            ("error", "Error"),
            ("disabled", "Disabled"),
        ],
        required=True,
        default="draft",
        copy=False,
        tracking=True,
    )
    last_test_date = fields.Datetime(readonly=True, copy=False)
    last_sync_date = fields.Datetime(readonly=True, copy=False)
    last_error = fields.Text(readonly=True, copy=False)
    active = fields.Boolean(default=True)
    auth_scheme = fields.Selection(
        [
            ("none", "No Authentication"),
            ("api_key_secret", "API Key + Secret"),
            ("basic", "HTTP Basic"),
            ("bearer", "Bearer Token"),
            ("oauth2_client_credentials", "OAuth 2.0 Client Credentials"),
            ("hmac", "Signed Webhook / HMAC"),
        ],
        required=True,
        default="none",
        tracking=True,
    )
    base_url = fields.Char(string="API Base URL")
    username = fields.Char(groups="bs_pos_delivery_core.group_delivery_integration_manager")
    api_key = EncryptedChar(groups="bs_pos_delivery_core.group_delivery_integration_manager")
    api_secret = EncryptedChar(groups="bs_pos_delivery_core.group_delivery_integration_manager")
    password = EncryptedChar(groups="bs_pos_delivery_core.group_delivery_integration_manager")
    oauth_token_url = fields.Char(string="OAuth Token URL")
    access_token = EncryptedChar(groups="bs_pos_delivery_core.group_delivery_integration_manager")
    access_token_expires_at = fields.Datetime(copy=False, readonly=True)
    merchant_id = fields.Char(string="Merchant / Integration ID", index=True)
    webhook_secret = EncryptedChar(groups="bs_pos_delivery_core.group_delivery_integration_manager")
    webhook_token = EncryptedChar(
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: self._generate_webhook_token(),
        groups="bs_pos_delivery_core.group_delivery_integration_manager",
    )
    webhook_token_digest = fields.Char(
        readonly=True,
        copy=False,
        index=True,
    )
    webhook_url = fields.Char(
        compute="_compute_webhook_url",
        groups="bs_pos_delivery_core.group_delivery_integration_manager",
    )
    webhook_max_attempts = fields.Integer(default=5, required=True)
    webhook_retry_base_seconds = fields.Integer(
        string="Webhook Retry Base (seconds)",
        default=60,
        required=True,
    )
    webhook_processing_timeout_minutes = fields.Integer(
        string="Processing Recovery Timeout (minutes)",
        default=15,
        required=True,
        help="A webhook left in Processing longer than this is returned to the retry queue.",
    )
    webhook_raw_payload_retention_days = fields.Integer(
        string="Raw Payload Retention (days)",
        default=7,
        required=True,
        help="Raw webhook bodies are erased after this period. Sanitized metadata remains.",
    )
    webhook_event_retention_days = fields.Integer(
        string="Completed Event Retention (days)",
        default=90,
        required=True,
        help="Completed and ignored webhook events older than this are removed.",
    )
    sync_log_retention_days = fields.Integer(
        string="Synchronization Log Retention (days)",
        default=90,
        required=True,
        help="Synchronization logs older than this are removed.",
    )
    outgoing_mode = fields.Selection(
        [("synchronous", "Synchronous"), ("queued", "Queued / Retryable")],
        default="synchronous",
        required=True,
        help=(
            "Queued mode durably retries provider-facing order status changes. "
            "Connection tests and read operations always run synchronously."
        ),
    )
    outbox_max_attempts = fields.Integer(default=5, required=True)
    outbox_retry_base_seconds = fields.Integer(
        string="Outgoing Retry Base (seconds)",
        default=60,
        required=True,
    )
    outbox_processing_timeout_minutes = fields.Integer(
        string="Outgoing Recovery Timeout (minutes)",
        default=15,
        required=True,
    )
    outbox_retention_days = fields.Integer(
        string="Completed Outgoing Job Retention (days)",
        default=90,
        required=True,
    )
    commission_pct = fields.Float(
        string="Default Commission %",
        digits=(16, 4),
        default=0.0,
    )
    sound_alert = fields.Boolean(default=True)
    auto_print_kitchen = fields.Boolean(default=False)
    tax_rounding_tolerance = fields.Float(
        string="Tax Rounding Tolerance",
        digits=(16, 2),
        default=5.0,
        help="Largest tax difference between the platform and Odoo accepted as "
             "rounding (the platform rounds the whole order, Odoo each line). "
             "Odoo's tax is then used and the difference is noted on the order. "
             "The order total must still match exactly.",
    )
    polling_interval_minutes = fields.Integer(
        string="Polling Interval (minutes)",
        default=2,
        required=True,
    )
    closed_pos_retry_timeout_minutes = fields.Integer(
        string="Closed POS Wait Timeout (minutes)",
        default=15,
        required=True,
        help="How long to wait for a closed POS session to open before failing or rejecting the order.",
    )
    closed_pos_max_retry_attempts = fields.Integer(
        string="Max Session Retry Attempts",
        default=5,
        required=True,
        help="Maximum retry attempts while waiting for a closed POS session to open.",
    )
    closed_pos_auto_reject_on_timeout = fields.Boolean(
        string="Auto-reject on Timeout",
        default=False,
        help="If enabled, orders whose POS session did not open before the deadline will be automatically rejected.",
    )
    paused = fields.Boolean(default=False, index=True, tracking=True)
    paused_until = fields.Datetime(copy=False)
    pause_reason = fields.Text(copy=False)
    is_paused = fields.Boolean(compute="_compute_is_paused")
    store_binding_ids = fields.One2many(
        "bs.delivery.store.binding",
        "connection_id",
        string="Remote Stores",
    )
    delivery_accept_action = fields.Selection(
        [
            ("accept", "Auto Accept"),
            ("manual", "Manual Review"),
            ("reject", "Auto Reject"),
        ],
        string="Delivery Order Action",
        default="manual",
        required=True,
    )
    delivery_default_invoice_partner_id = fields.Many2one(
        "res.partner", string="Default Delivery Invoice Customer",
        check_company=True,
    )
    delivery_auto_reject_reason_id = fields.Many2one(
        "bs.delivery.reject.reason", string="Automatic Delivery Rejection Reason",
        check_company=True,
    )
    delivery_payment_mapping_ids = fields.One2many(
        "bs.delivery.pos.payment.mapping", "connection_id",
        string="Aggregator Payment Methods", copy=False,
        groups="bs_pos_delivery_core.group_delivery_manager,base.group_system",
    )
    remote_store_ids_display = fields.Char(
        string="Remote Store ID(s)",
        compute="_compute_remote_store_ids_display",
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )

    today_order_count = fields.Integer(compute="_compute_today_metrics")
    today_gmv = fields.Monetary(
        compute="_compute_today_metrics",
        currency_field="currency_id",
    )
    binding_ids = fields.One2many(
        "bs.delivery.order.binding",
        "connection_id",
        string="Order Bindings",
    )
    sync_log_ids = fields.One2many(
        "bs.delivery.sync.log",
        "connection_id",
        string="Synchronization Logs",
    )
    webhook_event_ids = fields.One2many(
        "bs.delivery.webhook.event",
        "connection_id",
        string="Webhook Events",
    )
    outbox_job_ids = fields.One2many(
        "bs.delivery.outbox",
        "connection_id",
        string="Outgoing Jobs",
    )
    binding_count = fields.Integer(compute="_compute_integration_record_counts")
    delivery_order_count = fields.Integer(compute="_compute_integration_record_counts")
    product_mapping_count = fields.Integer(compute="_compute_integration_record_counts")
    sync_log_count = fields.Integer(compute="_compute_integration_record_counts")
    webhook_event_count = fields.Integer(compute="_compute_integration_record_counts")
    outbox_job_count = fields.Integer(compute="_compute_integration_record_counts")

    _tax_rounding_tolerance_range = models.Constraint(
        "CHECK(tax_rounding_tolerance >= 0 AND tax_rounding_tolerance <= 5)",
        "The tax rounding tolerance must be between 0 and 5.",
    )
    _name_unique = models.Constraint(
        "UNIQUE(company_id, provider_id, name)",
        "Connection names must be unique per provider and company.",
    )
    _webhook_token_digest_unique = models.Constraint(
        "UNIQUE(webhook_token_digest)",
        "Webhook tokens must be unique.",
    )

    @api.constrains("menu_pricelist_id", "company_id")
    def _check_menu_pricelist_currency(self):
        for connection in self:
            pricelist = connection.menu_pricelist_id
            if pricelist and pricelist.currency_id != connection.company_id.currency_id:
                raise ValidationError(_("Menu pricelist currency must match the connection company currency."))

    def _get_menu_pricelist(self):
        self.ensure_one()
        pricelist = self.sudo().menu_pricelist_id
        if (
            not pricelist
            or not pricelist.active
            or pricelist.currency_id != self.company_id.currency_id
            or (pricelist.company_id and pricelist.company_id != self.company_id)
        ):
            return pricelist.browse()
        return pricelist.with_company(self.company_id)

    @api.constrains(
        "delivery_accept_action", "delivery_default_invoice_partner_id",
        "delivery_auto_reject_reason_id", "company_id",
    )
    def _check_delivery_acceptance(self):
        for connection in self:
            if connection.delivery_accept_action == "accept" and not connection.delivery_default_invoice_partner_id:
                raise ValidationError(_("Choose a default invoice customer for automatic delivery acceptance."))
            partner = connection.delivery_default_invoice_partner_id
            if partner and partner.company_id and partner.company_id != connection.company_id:
                raise ValidationError(_("The default delivery customer belongs to another company."))
            if connection.delivery_accept_action == "reject":
                reason = connection.delivery_auto_reject_reason_id
                if not reason or not reason.active or reason.provider_id or reason.requires_note:
                    raise ValidationError(_("Choose an active provider-neutral rejection reason without a required note."))
                if reason.company_id and reason.company_id != connection.company_id:
                    raise ValidationError(_("The delivery rejection reason belongs to another company."))
    _webhook_policy_valid = models.Constraint(
        "CHECK(webhook_max_attempts > 0 "
        "AND webhook_retry_base_seconds > 0 "
        "AND webhook_processing_timeout_minutes > 0 "
        "AND webhook_raw_payload_retention_days >= 0 "
        "AND webhook_event_retention_days > 0 "
        "AND sync_log_retention_days > 0 "
        "AND outbox_max_attempts > 0 "
        "AND outbox_retry_base_seconds > 0 "
        "AND outbox_processing_timeout_minutes > 0 "
        "AND outbox_retention_days > 0 "
        "AND polling_interval_minutes > 0 "
        "AND commission_pct >= 0 AND commission_pct <= 100)",
        "Delivery queue retry and retention settings are invalid.",
    )

    @api.model
    def _generate_webhook_token(self):
        return secrets.token_urlsafe(32)

    @api.model
    def _webhook_token_digest_value(self, token):
        token = str(token or "")
        return hashlib.sha256(token.encode("utf-8")).hexdigest() if token else False

    @api.depends("paused", "paused_until")
    def _compute_is_paused(self):
        now = fields.Datetime.now()
        for connection in self:
            connection.is_paused = bool(
                connection.paused
                and (not connection.paused_until or connection.paused_until > now)
            )

    @api.depends("store_binding_ids.remote_store_id", "store_binding_ids.active")
    def _compute_remote_store_ids_display(self):
        for connection in self:
            connection.remote_store_ids_display = ", ".join(
                connection.store_binding_ids.filtered("active").mapped(
                    "remote_store_id"
                )
            )

    @api.depends(
        "provider_id",
        "provider_id.capabilities",
        "provider_id.implementation_state",
        "provider_id.active",
        "provider_id.module_id.state",
    )
    def _compute_menu_capabilities(self):
        for connection in self:
            connection.can_pull_menu = connection._supports_menu_operation("pull")
            connection.can_push_menu = connection._supports_menu_operation("push")

    def _supports_menu_operation(self, direction):
        self.ensure_one()
        operation = "pull_menu" if direction == "pull" else "push_menu"
        catalog_capability = "menu_pull" if direction == "pull" else "menu_push"
        provider = self.provider_id
        if not (
            provider.is_operational
            and provider.supports_catalog_capability(catalog_capability)
        ):
            return False
        model_name = provider.adapter_model_name
        if not model_name or not self.env.registry.get(model_name):
            return False
        return self.env[model_name].supports(operation)

    @api.depends_context("tz", "allowed_company_ids")
    def _compute_today_metrics(self):
        for connection in self:
            connection.today_order_count = 0
            connection.today_gmv = 0.0
        if not self:
            return
        # "Today" is the company's current business day (pos_business_day),
        # matching the Connect dashboard and POS business-day reports.
        business_today = {
            company: company._bs_current_business_date()
            for company in self.mapped("company_id")
        }
        if not business_today:
            return
        selected_date = fields.Date.context_today(self)
        domain = ["|"] * (len(business_today) - 1)
        for company, business_date in business_today.items():
            domain += [
                "&",
                ("company_id", "=", company.id),
                ("business_date", "=", fields.Date.to_string(business_date)),
            ]
        orders = self.env["bs.delivery.order"].search(
            [
                ("connection_id", "in", self.ids),
                ("state", "not in", ("rejected", "cancelled")),
            ]
            + domain
        )
        counts = {connection.id: 0 for connection in self}
        amounts = {connection.id: 0.0 for connection in self}
        for order in orders:
            connection = order.connection_id
            counts[connection.id] += 1
            currency = order.currency_id or order.company_id.currency_id
            amounts[connection.id] += currency._convert(
                order.amount_total,
                connection.currency_id,
                connection.company_id,
                fields.Date.to_date(order.received_at) or selected_date,
            )
        for connection in self:
            connection.today_order_count = counts[connection.id]
            connection.today_gmv = connection.currency_id.round(
                amounts[connection.id]
            )

    @api.depends("webhook_token")
    def _compute_webhook_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for connection in self:
            connection.webhook_url = (
                "%s/bs_delivery/webhook/%s" % (base_url.rstrip("/"), connection.webhook_token)
                if connection.webhook_token
                else False
            )

    @api.constrains("provider_id", "active", "environment")
    def _check_provider_is_installed(self):
        for connection in self:
            provider = connection.provider_id
            if (
                connection.active
                and provider.module_id
                and provider.module_state not in ("installed", "to install", "to upgrade")
            ):
                raise ValidationError(
                    _("Install %(provider)s before creating an active connection.")
                    % {"provider": provider.display_name}
                )
            if connection.active and provider.implementation_state in (
                "awaiting_api",
                "deprecated",
            ):
                raise ValidationError(
                    _(
                        "%(provider)s cannot be activated while its connector is %(state)s.",
                        provider=provider.display_name,
                        state=dict(provider._fields["implementation_state"].selection).get(
                            provider.implementation_state,
                            provider.implementation_state,
                        ),
                    )
                )
            if (
                connection.active
                and connection.environment == "production"
                and provider.implementation_state != "ready"
            ):
                raise ValidationError(
                    _("Only a production-ready connector can use the Production environment.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for incoming in vals_list:
            values = self._apply_secret_placeholder_values(
                incoming,
                self._SECRET_FIELDS,
            )
            token = values.get("webhook_token") or self._generate_webhook_token()
            values["webhook_token"] = token
            values["webhook_token_digest"] = self._webhook_token_digest_value(token)
            prepared.append(values)
        return super().create(prepared)

    def write(self, vals):
        values = self._apply_secret_placeholder_values(vals, self._SECRET_FIELDS)
        if values.get("webhook_token"):
            values["webhook_token_digest"] = self._webhook_token_digest_value(
                values["webhook_token"]
            )
        if values.get("paused") is False:
            values.setdefault("paused_until", False)
            values.setdefault("pause_reason", False)
        result = super().write(values)
        if "pos_config_ids" in values and not self.env.context.get(
            "bs_delivery_store_scope_sync"
        ):
            for connection in self:
                connection._synchronize_pos_scope_from_store_bindings(
                    authoritative=bool(
                        connection.with_context(active_test=False).store_binding_ids
                    )
                )
        return result

    def _synchronize_pos_scope_from_store_bindings(self, *, authoritative=False):
        """Project active store branches onto the legacy connection POS scope.

        Direct POS links may seed providers that create their stores afterwards.
        Once a store changes, its mapping is the source of truth instead.
        """
        for connection in self.sudo():
            active_pos_ids = connection.store_binding_ids.filtered("active").mapped(
                "pos_config_id"
            ).ids
            if authoritative:
                if set(active_pos_ids) != set(connection.pos_config_ids.ids):
                    connection.with_context(bs_delivery_store_scope_sync=True).write(
                        {"pos_config_ids": [Command.set(active_pos_ids)]}
                    )
                continue
            missing_ids = set(active_pos_ids) - set(connection.pos_config_ids.ids)
            if not missing_ids:
                continue
            connection.with_context(bs_delivery_store_scope_sync=True).write(
                {"pos_config_ids": [Command.link(pos_id) for pos_id in missing_ids]}
            )
        return True

    def read(self, fields=None, load="_classic_read"):
        result = super().read(fields=fields, load=load)
        requested = set(fields or self._fields)
        secret_names = requested & self._SECRET_FIELDS
        for values in result:
            for field_name in secret_names:
                if values.get(field_name):
                    values[field_name] = "********"
        return result

    def _compute_integration_record_counts(self):
        counts_by_field = {
            "binding_count": self._counts_by_connection("bs.delivery.order.binding"),
            "delivery_order_count": self._counts_by_connection("bs.delivery.order"),
            "product_mapping_count": self._counts_by_connection(
                "bs.delivery.product.binding"
            ),
            "sync_log_count": self._counts_by_connection("bs.delivery.sync.log"),
            "webhook_event_count": self._counts_by_connection("bs.delivery.webhook.event"),
            "outbox_job_count": self._counts_by_connection("bs.delivery.outbox"),
        }
        for connection in self:
            for field_name, counts in counts_by_field.items():
                connection[field_name] = counts.get(connection.id, 0)

    def _counts_by_connection(self, model_name):
        if not self.ids:
            return {}
        grouped = self.env[model_name]._read_group(
            [("connection_id", "in", self.ids)],
            ["connection_id"],
            ["__count"],
        )
        return {connection.id: count for connection, count in grouped}

    def _is_pos_board_eligible(self):
        self.ensure_one()
        return self.state == "connected"

    @api.model
    def _for_pos_board(self, config, provider=None):
        """Return eligible routes from active stores or legacy direct POS links.

        A direct link is used only when the connection has no store mappings.
        An active store still works when its legacy M2M link is missing.
        """
        config.ensure_one()
        base_domain = [
            ("company_id", "=", config.company_id.id),
            ("state", "in", ("connected", "draft")),
            ("active", "=", True),
            ("provider_id.active", "=", True),
            ("provider_id.connector_kind", "in", ("aggregator", "hybrid")),
            ("provider_id.implementation_state", "in", ("sandbox", "ready")),
        ]
        if provider:
            if getattr(provider, "_name", False):
                provider.ensure_one()
                provider_id = provider.id
            else:
                try:
                    provider_id = int(provider)
                except (TypeError, ValueError) as error:
                    raise ValidationError(_("Invalid delivery provider.")) from error
            base_domain.append(("provider_id", "=", provider_id))
        connection_model = self.sudo()
        direct = connection_model.search(
            base_domain + [("pos_config_ids", "in", config.id)]
        ).filtered(
            lambda connection: connection._is_pos_board_eligible()
            and not connection.with_context(active_test=False).store_binding_ids
        )
        store_connection_ids = (
            self.env["bs.delivery.store.binding"]
            .sudo()
            .search(
                [
                    ("pos_config_id", "=", config.id),
                    ("active", "=", True),
                ]
            )
            .mapped("connection_id")
            .ids
        )
        through_store = (
            connection_model.search(
                base_domain + [("id", "in", store_connection_ids)]
            ).filtered(lambda connection: connection._is_pos_board_eligible())
            if store_connection_ids
            else connection_model.browse()
        )
        return direct | through_store

    def _get_adapter(self):
        self.ensure_one()
        model_name = self.adapter_model_name
        if not model_name or not self.env.registry.get(model_name):
            raise DeliveryUnsupportedOperation(
                _(
                    "The %(provider)s adapter is not available. Install or upgrade its "
                    "provider module first."
                )
                % {"provider": self.provider_id.display_name}
            )
        return self.env[model_name]

    def _prepare_dispatch(self, operation):
        """Validate an adapter operation before any cross-cutting guard runs.

        Adapter extensions need the same deterministic
        diagnostics as Core.  Keeping the structural validation in one helper
        ensures a missing/disabled connector is reported before a policy layer
        evaluates an operation that could never be dispatched.
        """
        self.ensure_one()
        provider_state = self.provider_id.implementation_state
        if provider_state not in ("sandbox", "ready"):
            raise DeliveryUnsupportedOperation(
                _(
                    "%(provider)s is not operational while its connector is %(state)s.",
                    provider=self.provider_id.display_name,
                    state=provider_state,
                )
            )
        if self.environment == "production" and provider_state != "ready":
            raise DeliveryUnsupportedOperation(
                _("This connector is not approved for production operations.")
            )
        contract = self.env["bs.delivery.adapter"]
        if operation not in contract.OPERATIONS:
            raise DeliveryUnsupportedOperation(
                _("Unknown delivery adapter operation: %s") % operation
            )
        adapter = self._get_adapter()
        if not adapter.supports(operation):
            raise DeliveryUnsupportedOperation(
                _("%(provider)s does not support the %(operation)s operation.")
                % {
                    "provider": self.provider_id.display_name,
                    "operation": operation,
                }
            )
        return adapter

    def _dispatch(self, operation, *args, **kwargs):
        self.ensure_one()
        adapter = self._prepare_dispatch(operation)
        return getattr(adapter, operation)(self, *args, **kwargs)

    def action_test_connection(self):
        self.ensure_one()
        try:
            result = self._dispatch("test_connection")
        except DeliveryIntegrationError as error:
            self.write(
                {
                    "state": "error",
                    "last_test_date": fields.Datetime.now(),
                    "last_error": str(error),
                }
            )
            return self._notification(str(error), notification_type="danger")
        except Exception:
            _logger.exception("Unexpected error while testing delivery connection %s", self.id)
            message = _("The connection test failed unexpectedly. Check the server log.")
            self.write(
                {
                    "state": "error",
                    "last_test_date": fields.Datetime.now(),
                    "last_error": message,
                }
            )
            return self._notification(message, notification_type="danger")

        message = result.get("message") if isinstance(result, dict) else False
        self.write(
            {
                "state": "connected",
                "last_test_date": fields.Datetime.now(),
                "last_error": False,
            }
        )
        return self._notification(
            message or _("Connection established successfully."),
            notification_type="success",
        )

    def action_reset_to_draft(self):
        self.write({"state": "draft", "last_error": False})
        return True

    def action_pull_menu(self):
        return self._action_menu_synchronization("pull")

    def action_push_menu(self):
        return self._action_menu_synchronization("push")

    def action_sync_all_stores_availability(self):
        self.ensure_one()
        adapter = self._get_adapter()
        if not adapter.supports("update_store_availability"):
            return self._notification(
                _("%s does not support store availability updates.")
                % self.provider_id.display_name,
                notification_type="warning",
            )
        opened = closed = 0
        for store in self.store_binding_ids.filtered("active"):
            is_open = store.is_open_for_delivery()
            store._push_availability(adapter, is_open)
            if is_open:
                opened += 1
            else:
                closed += 1
        return self._notification(
            _("Store availability sent: %(opened)s open, %(closed)s closed.",
              opened=opened, closed=closed),
            notification_type="success",
        )

    def _action_menu_synchronization(self, direction):
        self.ensure_one()
        if self.state != "connected":
            raise ValidationError(
                _("Test and connect the platform before synchronizing its menu.")
            )
        if not self._supports_menu_operation(direction):
            raise DeliveryUnsupportedOperation(
                _(
                    "%(provider)s does not support %(direction)s menu synchronization.",
                    provider=self.provider_id.display_name,
                    direction=direction,
                )
            )

        stores = self.store_binding_ids.filtered("active")
        scopes = stores or self.env["bs.delivery.store.binding"]
        values_list = []
        if scopes:
            for store in scopes:
                values_list.append(
                    {
                        "name": _(
                            "%(platform)s %(direction)s menu - %(store)s",
                            platform=self.provider_id.display_name,
                            direction=direction.title(),
                            store=store.display_name,
                        ),
                        "connection_id": self.id,
                        "store_binding_id": store.id,
                        "direction": direction,
                    }
                )
        else:
            values_list.append(
                {
                    "name": _(
                        "%(platform)s %(direction)s menu",
                        platform=self.provider_id.display_name,
                        direction=direction.title(),
                    ),
                    "connection_id": self.id,
                    "direction": direction,
                }
            )
        versions = self.env["bs.delivery.menu.version"].create(values_list)
        versions.action_run_now()
        action = self.env.ref(
            "bs_pos_delivery_core.action_bs_delivery_menu_version"
        ).read()[0]
        action["domain"] = [("id", "in", versions.ids)]
        if len(versions) == 1:
            form_view = self.env.ref(
                "bs_pos_delivery_core.view_bs_delivery_menu_version_form"
            )
            action.update(
                {
                    "res_id": versions.id,
                    "views": [(form_view.id, "form")],
                    "view_mode": "form",
                }
            )
        return action

    def pause_receiving(self, *, reason=None, until=None):
        self._check_operational_management_access()
        return self.sudo().write(
            {
                "paused": True,
                "paused_until": until or False,
                "pause_reason": str(reason or "").strip() or False,
            }
        )

    def resume_receiving(self):
        self._check_operational_management_access()
        return self.sudo().write(
            {
                "paused": False,
                "paused_until": False,
                "pause_reason": False,
            }
        )

    def _check_operational_management_access(self):
        """Allow restaurant managers to operate the rush-hour switch only.

        Connection credentials remain protected by the read-only connection
        ACL for delivery managers.  This explicit, narrowly scoped method
        makes the visible pause/resume buttons usable without granting broad
        write access to authentication or routing configuration.
        """
        if not self.env.user.has_group(
            "bs_pos_delivery_core.group_delivery_manager"
        ):
            raise AccessError(_("Delivery manager access is required."))
        self.check_access("read")
        if any(connection.company_id not in self.env.companies for connection in self):
            raise AccessError(_("The delivery connection is not available."))
        return True

    def is_receiving_paused(self):
        self.ensure_one()
        return bool(not self.active or self.state == "disabled" or self.is_paused)

    def action_view_order_bindings(self):
        self.ensure_one()
        action = self.env.ref(
            "bs_pos_delivery_core.action_bs_delivery_order_binding"
        ).read()[0]
        action["domain"] = [("connection_id", "=", self.id)]
        action["context"] = {"default_connection_id": self.id}
        return action

    def action_view_delivery_orders(self):
        self.ensure_one()
        action = self.env.ref("bs_pos_delivery_core.action_bs_delivery_order").read()[0]
        action["domain"] = [("connection_id", "=", self.id)]
        return action

    def action_view_product_mappings(self):
        self.ensure_one()
        action = self.env.ref(
            "bs_pos_delivery_core.action_bs_delivery_product_binding"
        ).read()[0]
        action["domain"] = [("connection_id", "=", self.id)]
        action["context"] = {
            "default_connection_id": self.id,
            "default_company_id": self.company_id.id,
        }
        return action

    def action_view_sync_logs(self):
        self.ensure_one()
        action = self.env.ref("bs_pos_delivery_core.action_bs_delivery_sync_log").read()[0]
        action["domain"] = [("connection_id", "=", self.id)]
        action["context"] = {"default_connection_id": self.id}
        return action

    def action_view_webhook_events(self):
        self.ensure_one()
        action = self.env.ref(
            "bs_pos_delivery_core.action_bs_delivery_webhook_event"
        ).read()[0]
        action["domain"] = [("connection_id", "=", self.id)]
        return action

    def action_view_outbox_jobs(self):
        self.ensure_one()
        action = self.env.ref("bs_pos_delivery_core.action_bs_delivery_outbox").read()[0]
        action["domain"] = [("connection_id", "=", self.id)]
        action["context"] = {"default_connection_id": self.id}
        return action

    def dispatch_order_operation(
        self,
        operation,
        binding,
        *,
        status_value=None,
        reason=None,
        idempotency_key=None,
    ):
        """Run a mutation now or durably enqueue it according to connection policy."""
        self.ensure_one()
        binding.ensure_one()
        outbox = self.env["bs.delivery.outbox"]
        if operation not in outbox.MUTATING_OPERATIONS:
            raise DeliveryUnsupportedOperation(
                _("This delivery operation cannot use the outgoing queue: %s") % operation
            )
        if self.outgoing_mode == "queued":
            job, created = outbox.enqueue(
                self,
                operation,
                binding,
                status_value=status_value,
                reason=reason,
                idempotency_key=idempotency_key,
            )
            return {
                "queued": True,
                "created": created,
                "job_id": job.id,
                "correlation_id": job.correlation_id,
            }
        if operation == "update_order":
            return self._dispatch(
                operation,
                binding,
                status_value,
                reason=reason,
            )
        if operation in ("reject_order", "cancel_order"):
            return self._dispatch(operation, binding, reason=reason)
        return self._dispatch(operation, binding)

    def _secret_placeholder(self, field_name):
        """Return a stable masked value without exposing the stored credential."""
        self.ensure_one()
        return "********" if self[field_name] else False

    def _apply_secret_placeholder_values(self, values, field_names):
        """Treat the unchanged UI placeholder as 'keep the current secret'."""
        values = dict(values)
        for field_name in field_names:
            if values.get(field_name) == "********":
                values.pop(field_name)
        return values

    def action_rotate_webhook_token(self):
        if not self.env.user.has_group(
            "bs_pos_delivery_core.group_delivery_integration_manager"
        ):
            raise AccessError(_("Only a delivery integration administrator can rotate it."))
        self.write({"webhook_token": self._generate_webhook_token()})
        return self._notification(
            _("A new webhook URL has been generated. Update it at the provider."),
            notification_type="warning",
        )

    def _notification(self, message, *, notification_type):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Delivery Connection"),
                "message": message,
                "type": notification_type,
                "sticky": notification_type == "danger",
            },
        }
