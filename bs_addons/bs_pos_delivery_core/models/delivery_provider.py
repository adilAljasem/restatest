import base64
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.modules.module import Manifest
from odoo.tools import file_open
from odoo.tools.image import image_data_uri, image_process


_logger = logging.getLogger(__name__)


class DeliveryProvider(models.Model):
    """Catalog entry describing provider software, never its credentials."""

    _name = "bs.delivery.provider"
    _description = "POS Delivery Provider"
    _order = "sequence, name, id"

    _MODULE_PREFIX = "bs_pos_delivery_"
    _CORE_MODULE = "bs_pos_delivery_core"
    _NON_PROVIDER_MODULES = frozenset(
        {
            "bs_pos_delivery_pos",
            "bs_pos_delivery_ui",
        }
    )
    _CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
    _COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
    _CAPABILITY_CODES = frozenset(
        {
            "incoming_orders",
            "outgoing_orders",
            "order_status",
            "menu_pull",
            "menu_push",
            "store_availability",
            "driver_tracking",
            "loyalty",
        }
    )

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    sequence = fields.Integer(default=10)
    image_128 = fields.Image(max_width=128, max_height=128)
    description = fields.Text(translate=True)
    website = fields.Char()
    connector_kind = fields.Selection(
        [
            ("aggregator", "Food Aggregator"),
            ("loyalty", "Loyalty / Customer Engagement"),
            ("hybrid", "Aggregator and Loyalty"),
        ],
        required=True,
        default="aggregator",
        index=True,
    )
    implementation_state = fields.Selection(
        [
            ("awaiting_api", "Awaiting Official API"),
            ("development", "In Development"),
            ("sandbox", "Sandbox Ready"),
            ("ready", "Production Ready"),
            ("deprecated", "Deprecated"),
        ],
        required=True,
        default="ready",
        index=True,
    )
    capabilities = fields.Json(
        default=dict,
        help=(
            "Public connector capabilities. Values must be booleans keyed by one of: "
            "incoming_orders, outgoing_orders, order_status, menu_pull, menu_push, "
            "store_availability, driver_tracking, loyalty."
        ),
    )
    color = fields.Char(
        default="#7c3aed",
        help="Six-digit hexadecimal color used by the POS delivery board.",
    )
    is_operational = fields.Boolean(
        compute="_compute_is_operational",
    )
    active = fields.Boolean(default=True)

    module_id = fields.Many2one(
        "ir.module.module",
        string="Corresponding Module",
        readonly=True,
    )
    companion_module_ids = fields.Many2many(
        "ir.module.module",
        "bs_delivery_provider_companion_module_rel",
        "provider_id",
        "module_id",
        string="Owned Companion Modules",
        readonly=True,
        help=(
            "Provider-specific modules that must be removed with the adapter. "
            "Shared Odoo dependencies must not be listed here."
        ),
    )
    module_state = fields.Selection(
        related="module_id.state",
        string="Installation State",
        readonly=True,
    )
    module_available = fields.Boolean(
        string="Module Available on Server",
        compute="_compute_module_available",
        search="_search_module_available",
        compute_sudo=True,
    )
    has_installed_companion_modules = fields.Boolean(
        compute="_compute_connection_status",
    )
    adapter_model_name = fields.Char(compute="_compute_adapter_model_name")

    connection_count = fields.Integer(compute="_compute_connection_status")
    connected_connection_count = fields.Integer(compute="_compute_connection_status")
    configuration_state = fields.Selection(
        [
            ("not_installed", "Not Installed"),
            ("cleanup_required", "Cleanup Required"),
            ("not_configured", "Not Configured"),
            ("configured", "Configured"),
            ("connected", "Connected"),
            ("error", "Connection Error"),
        ],
        compute="_compute_connection_status",
        string="Configuration Status",
    )

    _code_unique = models.Constraint(
        "UNIQUE(code)",
        "The delivery provider code must be unique.",
    )
    _module_unique = models.Constraint(
        "UNIQUE(module_id)",
        "A module can only be linked to one delivery provider.",
    )

    @api.depends("code")
    def _compute_adapter_model_name(self):
        for provider in self:
            provider.adapter_model_name = (
                "bs.delivery.adapter.%s" % provider.code if provider.code else False
            )

    @staticmethod
    def _module_is_available(module_name):
        manifest = Manifest.for_addon(module_name, display_warning=False)
        return bool(manifest and manifest.get("installable", True))

    @api.depends("module_id.name")
    def _compute_module_available(self):
        for provider in self:
            provider.module_available = not provider.module_id or self._module_is_available(
                provider.module_id.name
            )

    @api.model
    def _search_module_available(self, operator, value):
        if operator != "in":
            return NotImplemented
        if not value:
            return [("id", "in", [])]
        if True in value and False in value:
            return []
        # Catalog metadata is global. Only inspect module-backed entries; keep
        # the caller's ACLs and record rules on the resulting provider search.
        providers = self.sudo().with_context(active_test=False).search(
            [("module_id", "!=", False)]
        )
        unavailable = providers.filtered(lambda provider: not provider.module_available)
        return [("id", "not in" if True in value else "in", unavailable.ids)]

    @api.depends("active", "implementation_state", "module_state", "module_id", "module_available")
    def _compute_is_operational(self):
        for provider in self:
            module_ready = not provider.module_id or provider.module_state in (
                "installed",
                "to install",
                "to upgrade",
            )
            provider.is_operational = bool(
                provider.active
                and provider.module_available
                and module_ready
                and provider.implementation_state in ("sandbox", "ready")
            )

    @api.depends("module_state", "companion_module_ids.state")
    def _compute_connection_status(self):
        counts = {provider.id: {"total": 0, "connected": 0, "error": 0} for provider in self}
        if self.ids:
            connections = self.env["bs.delivery.connection"].search(
                [
                    ("provider_id", "in", self.ids),
                    ("company_id", "in", self.env.companies.ids),
                    ("active", "=", True),
                ]
            )
            for connection in connections:
                values = counts[connection.provider_id.id]
                values["total"] += 1
                if connection.state == "connected":
                    values["connected"] += 1
                elif connection.state == "error":
                    values["error"] += 1

        for provider in self:
            values = counts.get(provider.id, {"total": 0, "connected": 0, "error": 0})
            provider.connection_count = values["total"]
            provider.connected_connection_count = values["connected"]
            provider.has_installed_companion_modules = any(
                module.state in ("installed", "to upgrade")
                for module in provider.companion_module_ids
            )
            if provider.module_id and provider.module_state != "installed":
                provider.configuration_state = (
                    "cleanup_required"
                    if provider.has_installed_companion_modules
                    else "not_installed"
                )
            elif not values["total"]:
                provider.configuration_state = "not_configured"
            elif values["connected"]:
                provider.configuration_state = "connected"
            elif values["error"] == values["total"]:
                provider.configuration_state = "error"
            else:
                provider.configuration_state = "configured"

    @api.constrains("code", "color", "capabilities")
    def _check_catalog_metadata(self):
        for provider in self:
            if not self._CODE_PATTERN.fullmatch(provider.code or ""):
                raise ValidationError(
                    _(
                        "Provider codes must start with a lowercase letter and only contain "
                        "lowercase letters, numbers, and underscores."
                    )
                )
            if provider.color and not self._COLOR_PATTERN.fullmatch(provider.color):
                raise ValidationError(
                    _("The provider color must use the #RRGGBB format.")
                )
            capabilities = provider.capabilities or {}
            if not isinstance(capabilities, dict):
                raise ValidationError(_("Provider capabilities must be a JSON object."))
            unknown = sorted(set(capabilities) - self._CAPABILITY_CODES)
            invalid_values = sorted(
                key for key, value in capabilities.items() if not isinstance(value, bool)
            )
            if unknown:
                raise ValidationError(
                    _("Unknown provider capabilities: %s") % ", ".join(unknown)
                )
            if invalid_values:
                raise ValidationError(
                    _("Provider capability values must be true or false: %s")
                    % ", ".join(invalid_values)
                )

    def supports_catalog_capability(self, capability):
        self.ensure_one()
        capability = str(capability or "").strip()
        return bool((self.capabilities or {}).get(capability))

    @api.model
    def _sync_available_provider_modules(self):
        """Discover provider addons by technical-name convention.

        Updating the Apps list remains an explicit administrator operation. This
        method only mirrors already-known ``ir.module.module`` records whose
        installable manifests still exist on the server. Historical database
        records for removed addons must never recreate or reactivate cards.
        """
        module_model = self.env["ir.module.module"].sudo()
        modules = module_model.search(
            [
                ("name", "=like", "%s%%" % self._MODULE_PREFIX),
                ("name", "!=", self._CORE_MODULE),
                ("name", "not in", list(self._NON_PROVIDER_MODULES)),
                ("state", "!=", "uninstallable"),
            ]
        ).filtered(lambda module: self._module_is_available(module.name))
        provider_model = self.sudo().with_context(active_test=False)
        providers = provider_model.search(
            [
                "|",
                ("module_id", "in", modules.ids),
                ("code", "in", self._codes_from_modules(modules)),
            ]
        )
        by_module = {
            provider.module_id.id: provider for provider in providers if provider.module_id
        }
        by_code = {provider.code: provider for provider in providers}

        # Catalog entries can predate a connector's branded icon, including
        # uninstalled connectors whose registration data never runs. Replace
        # only Odoo's placeholder; retain any previously customized logo.
        with file_open("base/static/description/icon.png", "rb") as icon_file:
            placeholder = base64.b64encode(
                image_process(icon_file.read(), size=(128, 128))
            )

        for module in modules:
            code = module.name.removeprefix(self._MODULE_PREFIX)
            if not code or not self._CODE_PATTERN.fullmatch(code):
                _logger.warning("Ignoring invalid delivery provider module name: %s", module.name)
                continue
            provider = by_module.get(module.id) or by_code.get(code)
            if provider:
                values = {"module_id": module.id}
                if (
                    (not provider.image_128 or provider.image_128 == placeholder)
                    and module.icon_image
                ):
                    values["image_128"] = module.icon_image
                if not provider.active:
                    values["active"] = True
                provider.write(values)
                continue
            provider = provider_model.create(
                {
                    "name": module.shortdesc or code.replace("_", " ").title(),
                    "code": code,
                    "module_id": module.id,
                    "description": module.summary or module.description,
                    "website": module.website,
                    "image_128": module.icon_image,
                }
            )
            by_module[module.id] = provider
            by_code[code] = provider
        discovered = providers | provider_model.browse(
            [provider.id for provider in by_code.values()]
        )
        self._register_compatibility_xmlids(discovered)
        return discovered

    @api.model
    def _register_compatibility_xmlids(self, providers):
        """Keep stable Core-owned XML IDs for provider catalog records.

        Connector addons register metadata, while Core owns only the neutral
        catalog identifier. This preserves historical connections on uninstall.
        """
        data = []
        for provider in providers.filtered("module_id"):
            if provider.module_id.name != "%s%s" % (
                self._MODULE_PREFIX,
                provider.code,
            ):
                continue
            data.append(
                {
                    "xml_id": "bs_pos_delivery_core.provider_%s" % provider.code,
                    "record": provider,
                    "noupdate": True,
                }
            )
        self.env["ir.model.data"].sudo()._update_xmlids(data, update=True)

    @api.model
    def register_provider(self, code, values=None, module_name=None):
        """Register provider metadata without coupling Core to connector names.

        The connector calls this method from its data file.  The stable catalog
        record deliberately keeps a Core XML-ID so uninstalling the connector
        never tries to delete a provider referenced by historical connections.
        """
        code = str(code or "").strip().lower()
        if not self._CODE_PATTERN.fullmatch(code):
            raise ValidationError(_("Invalid delivery provider code: %s") % code)
        module_name = module_name or "%s%s" % (self._MODULE_PREFIX, code)
        module = self.env["ir.module.module"].sudo().search(
            [("name", "=", module_name)],
            limit=1,
        )
        if not module:
            raise ValidationError(
                _("Delivery provider module %s is not present in the Apps registry.")
                % module_name
            )

        allowed_fields = {
            "name",
            "sequence",
            "description",
            "website",
            "active",
            "connector_kind",
            "implementation_state",
            "capabilities",
            "color",
        }
        provider_values = {
            key: value for key, value in dict(values or {}).items() if key in allowed_fields
        }
        provider_values.update({"code": code, "module_id": module.id})
        provider_values.setdefault("active", True)
        if module.icon_image:
            provider_values["image_128"] = module.icon_image

        provider = self.sudo().with_context(active_test=False).search(
            [("code", "=", code)],
            limit=1,
        )
        if provider:
            provider.write(provider_values)
        else:
            provider = self.sudo().create(provider_values)
        self._register_compatibility_xmlids(provider)
        return provider

    @api.model
    def _codes_from_modules(self, modules):
        return [
            module.name.removeprefix(self._MODULE_PREFIX)
            for module in modules
            if module.name.startswith(self._MODULE_PREFIX)
        ]

    @api.model
    def action_open_provider_catalog(self):
        self._sync_available_provider_modules()
        action = self.env.ref("bs_pos_delivery_core.action_bs_delivery_provider").read()[0]
        action["domain"] = [("module_available", "=", True)]
        return action

    @api.model
    def configure_companion_modules(self, provider_ids, module_ids):
        """Apply owned-component metadata even to provider records marked noupdate."""
        providers = self.browse(provider_ids).exists()
        modules = self.env["ir.module.module"].browse(module_ids).exists()
        providers.write({"companion_module_ids": [(6, 0, modules.ids)]})
        return True

    def _check_module_management_access(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only a system administrator can install or uninstall providers."))

    def action_install_module(self):
        self.ensure_one()
        self._check_module_management_access()
        if not self.module_id:
            raise UserError(_("No installable module is linked to this provider."))
        if not self.module_available:
            raise UserError(_("This provider module is no longer available on the server."))
        if self.module_state != "installed":
            self.module_id.button_immediate_install()
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_uninstall_module(self):
        self.ensure_one()
        self._check_module_management_access()
        if not self.module_id:
            return {"type": "ir.actions.client", "tag": "reload"}
        if self.module_id.name == self._CORE_MODULE:
            raise UserError(_("The delivery core module cannot uninstall itself."))

        modules = (self.module_id | self.companion_module_ids).filtered(
            lambda module: module.state in ("installed", "to upgrade")
        )
        if not modules:
            return {"type": "ir.actions.client", "tag": "reload"}

        external_dependents = self.companion_module_ids.downstream_dependencies() - modules
        if external_dependents:
            raise UserError(
                _(
                    "Cannot fully uninstall %(provider)s because these installed modules "
                    "still use its companion components: %(modules)s",
                    provider=self.display_name,
                    modules=", ".join(external_dependents.mapped("shortdesc")),
                )
            )

        self.env["bs.delivery.connection"].sudo().search(
            [("provider_id", "=", self.id), ("active", "=", True)]
        ).write(
            {
                "active": False,
                "state": "disabled",
                "last_error": _("The provider module was uninstalled."),
            }
        )
        modules.button_immediate_uninstall()
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_configure(self):
        self.ensure_one()
        if not self.module_available:
            raise UserError(_("This provider module is no longer available on the server."))
        if self.implementation_state in ("awaiting_api", "deprecated"):
            raise UserError(
                _(
                    "%(provider)s is not available for configuration yet (%(state)s).",
                    provider=self.display_name,
                    state=dict(self._fields["implementation_state"].selection).get(
                        self.implementation_state, self.implementation_state
                    ),
                )
            )
        if self.module_id and self.module_state != "installed":
            raise UserError(_("Install the provider before configuring a connection."))

        action = self.env.ref("bs_pos_delivery_core.action_bs_delivery_connection").read()[0]
        connections = self.env["bs.delivery.connection"].search(
            [
                ("provider_id", "=", self.id),
                ("company_id", "in", self.env.companies.ids),
            ]
        )
        action["context"] = {
            "default_provider_id": self.id,
            "default_company_id": self.env.company.id,
        }
        if not connections:
            form_view = self.env.ref("bs_pos_delivery_core.view_bs_delivery_connection_form")
            action.update({"views": [(form_view.id, "form")], "view_mode": "form"})
        elif len(connections) == 1:
            form_view = self.env.ref("bs_pos_delivery_core.view_bs_delivery_connection_form")
            action.update(
                {
                    "res_id": connections.id,
                    "views": [(form_view.id, "form")],
                    "view_mode": "form",
                }
            )
        else:
            action["domain"] = [("id", "in", connections.ids)]
        return action

    def _pos_logo_data_url(self):
        self.ensure_one()
        if not self.image_128:
            return False
        encoded = self.image_128
        if isinstance(encoded, str):
            encoded = encoded.encode("ascii")
        return image_data_uri(encoded)

    @api.model
    def get_pos_provider_badges(self, pos_config_id):
        """Return the small provider snapshot consumed by the generic POS hub."""
        config = self.env["bs.delivery.order"]._check_pos_board_access(pos_config_id)

        connections = self.env["bs.delivery.connection"]._for_pos_board(config)
        installed = self.sudo().search([
            ("active", "=", True),
            ("connector_kind", "in", ("aggregator", "hybrid")),
            ("module_id.state", "=", "installed"),
        ]).filtered("module_available")
        providers = (connections.mapped("provider_id") | installed).sorted(
            key=lambda provider: (provider.sequence, provider.name or "", provider.id)
        )
        stores = self.env["bs.delivery.store.binding"].sudo().search(
            [
                ("connection_id", "in", connections.ids),
                ("pos_config_id", "=", config.id),
                ("active", "=", True),
            ]
        )
        store_map = {}
        for store in stores:
            store_map.setdefault(store.connection_id.id, self.env["bs.delivery.store.binding"])
            store_map[store.connection_id.id] |= store

        order_model = self.env["bs.delivery.order"].sudo()
        values = []
        polling_intervals = []
        for provider in providers:
            provider_connections = connections.filtered(
                lambda connection: connection.provider_id == provider
            )
            route_paused = []
            route_sounds = []
            for connection in provider_connections:
                polling_intervals.append(connection.polling_interval_minutes)
                connection_stores = store_map.get(
                    connection.id, self.env["bs.delivery.store.binding"]
                )
                if connection_stores:
                    for store in connection_stores:
                        route_paused.append(
                            connection.is_receiving_paused() or store.is_paused
                        )
                        route_sounds.append(bool(store.sound_alert))
                else:
                    route_paused.append(connection.is_receiving_paused())
                    route_sounds.append(bool(connection.sound_alert))
            base_domain = [
                ("provider_id", "=", provider.id),
                ("connection_id", "in", provider_connections.ids),
                ("company_id", "=", config.company_id.id),
                ("pos_config_id", "=", config.id),
            ]
            open_count = order_model.search_count(
                base_domain
                + [("state", "in", ("received", "accepted", "preparing", "ready", "picked_up"))]
            )
            received_count = order_model.search_count(
                base_domain + [("state", "=", "received")]
            )
            values.append(
                {
                    "id": provider.id,
                    "code": provider.code,
                    "name": provider.display_name,
                    "logo_url": provider._pos_logo_data_url(),
                    "color": provider.color,
                    "active": provider.active,
                    "connected": bool(provider_connections),
                    "open_count": open_count,
                    "received_count": received_count,
                    "paused": bool(route_paused) and all(route_paused),
                    "partially_paused": bool(route_paused)
                    and any(route_paused)
                    and not all(route_paused),
                    "sound_alert": any(route_sounds),
                    "polling_interval_minutes": min(
                        provider_connections.mapped("polling_interval_minutes") or [2]
                    ),
                }
            )

        recent_orders = order_model.search(
            [
                ("connection_id", "in", connections.ids),
                ("company_id", "=", config.company_id.id),
                ("pos_config_id", "=", config.id),
                ("state", "=", "received"),
            ],
            order="received_at desc, id desc",
            limit=50,
        )
        return {
            "providers": values,
            "polling_interval_minutes": min(polling_intervals or [2]),
            "recent_received_orders": [
                order._pos_notification_values() for order in recent_orders
            ],
        }

    @api.ondelete(at_uninstall=False)
    def _unlink_except_unmanaged_providers(self):
        managed = self.filtered("module_id")
        if managed:
            raise UserError(
                _(
                    "Module-backed providers cannot be deleted. Archive them or uninstall "
                    "the module."
                )
            )
