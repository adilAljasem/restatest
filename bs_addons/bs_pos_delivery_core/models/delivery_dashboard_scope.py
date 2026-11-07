"""Read-only dashboard scopes, drilldowns and operational measurements."""

import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import SQL


# Display names for the real ordering platforms that integration middleware
# (Blend, …) forwards in ``external_source``.  Keys are compacted (A-Z0-9).
CHANNEL_LABELS = {
    "HUNGER": "HungerStation",
    "HUNGERSTATION": "HungerStation",
    "JAHEZ": "Jahez",
    "KEETA": "Keeta",
    "MRSOOL": "Mrsool",
    "MARSOOL": "Mrsool",
    "TOYOU": "ToYou",
    "CAREEM": "Careem",
    "CAREEMNOW": "Careem",
    "CAREEMFOOD": "Careem",
    "NINJA": "Ninja",
    "THECHEFZ": "The Chefz",
    "CHEFZ": "The Chefz",
    "NOON": "Noon Food",
    "NOONFOOD": "Noon Food",
    "TALABAT": "Talabat",
    "LUGMETY": "Lugmety",
    "SHGARDI": "Shgardi",
    "DELIVEROO": "Deliveroo",
    "UBEREATS": "Uber Eats",
}
CHANNEL_COLORS = {
    "HUNGERSTATION": "#f2c200",
    "JAHEZ": "#d62a2a",
    "KEETA": "#f5b700",
    "MRSOOL": "#1f9e57",
    "TOYOU": "#ff6a13",
    "CAREEM": "#2fb34a",
    "NINJA": "#5b2d8e",
    "THECHEFZ": "#1d1d1b",
    "NOONFOOD": "#e8c300",
    "TALABAT": "#ff5a00",
}
CHANNEL_ALIASES = {
    "HUNGER": "HUNGERSTATION",
    "MARSOOL": "MRSOOL",
    "CAREEMNOW": "CAREEM",
    "CAREEMFOOD": "CAREEM",
    "CHEFZ": "THECHEFZ",
    "NOON": "NOONFOOD",
}


class DeliveryDashboardScope(models.AbstractModel):
    _inherit = "bs.delivery.dashboard"

    # ------------------------------------------------------------------
    # Ordering platform ("channel") helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _channel_compact(value):
        return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

    def _channel_key(self, source, provider):
        """Stable key of the real ordering platform of an order.

        Middleware such as Blend stores the originating platform (HUNGER,
        Jahez, keeta…) in ``external_source``; direct connectors store their
        own code or nothing, in which case the provider is the platform.
        """
        key = self._channel_compact(source) or self._channel_compact(provider.code)
        return CHANNEL_ALIASES.get(key, key)

    def _channel_is_provider(self, key, provider):
        return bool(provider) and key == self._channel_compact(provider.code)

    def _channel_label(self, key, provider=None):
        if provider and self._channel_is_provider(key, provider):
            return provider.display_name
        return CHANNEL_LABELS.get(key) or (key.title() if key else _("Unknown platform"))

    @staticmethod
    def _channel_color(key):
        return CHANNEL_COLORS.get(key, "")

    def _channel_domain(self, channel, companies):
        """Orders whose ordering platform is ``channel`` (case/format-insensitive)."""
        key = CHANNEL_ALIASES.get(self._channel_compact(channel), self._channel_compact(channel))
        sources, providers = set(), set()
        for source, provider, _count in self.env["bs.delivery.order"]._read_group(
            [("company_id", "in", companies.ids)], ["external_source", "provider_id"], ["__count"]
        ):
            if self._channel_key(source, provider) != key:
                continue
            if self._channel_compact(source):
                sources.add(source)
            elif provider:
                providers.add(provider.id)
        parts = []
        if sources:
            parts.append([("external_source", "in", sorted(sources))])
        if providers:
            parts.append(
                [
                    "&",
                    ("provider_id", "in", sorted(providers)),
                    "|",
                    ("external_source", "=", False),
                    ("external_source", "=", ""),
                ]
            )
        if not parts:
            return [("id", "=", 0)]
        if len(parts) == 1:
            return parts[0]
        return ["|", *parts[0], *parts[1]]

    def _dashboard_channels(self, companies):
        channels = {}
        for source, provider, count in self.env["bs.delivery.order"]._read_group(
            [("company_id", "in", companies.ids)], ["external_source", "provider_id"], ["__count"]
        ):
            key = self._channel_key(source, provider)
            if not key:
                continue
            item = channels.setdefault(
                key, {"key": key, "name": self._channel_label(key, provider), "count": 0}
            )
            item["count"] += count
        return sorted(channels.values(), key=lambda item: item["name"].casefold())

    def _dashboard_access(self):
        if not self.env.user.has_group("bs_pos_delivery_core.group_delivery_viewer"):
            raise AccessError(_("Delivery dashboard access is required."))

    def _dashboard_filters(self, filters=None):
        self._dashboard_access()
        if filters is not None and not isinstance(filters, dict):
            raise ValidationError(_("Invalid dashboard filters."))
        values = dict(filters or {})
        companies = self._allowed_companies(values.get("company_ids"))
        for key, model in (
            ("provider_id", "bs.delivery.provider"),
            ("store_binding_id", "bs.delivery.store.binding"),
            ("pos_config_id", "pos.config"),
        ):
            if not values.get(key):
                continue
            try:
                record_id = int(values[key])
            except (ValueError, TypeError) as error:
                raise ValidationError(_("Invalid dashboard selection.")) from error
            domain = [("id", "=", record_id)]
            if key != "provider_id":
                domain.append(("company_id", "in", companies.ids))
            record = self.env[model].with_context(active_test=False).search(domain, limit=1)
            if not record:
                raise AccessError(
                    _("The dashboard selection is not available in this company scope.")
                )
            values[key] = record_id
        if values.get("environment") not in (None, False, "", "test", "production"):
            raise ValidationError(_("Invalid dashboard environment."))
        channel = values.get("channel")
        if channel not in (None, False, ""):
            if not isinstance(channel, str) or len(channel) > 64 or not self._channel_compact(channel):
                raise ValidationError(_("Invalid dashboard platform."))
        return values, companies

    def _dashboard_anchor_date(self, companies=None):
        """Current *business* date (pos_business_day) of the dashboard scope."""
        company = (companies or self.env.company)[:1] or self.env.company
        return company._bs_current_business_date()

    def _dashboard_period(self, filters, companies=None):
        try:
            anchor = fields.Date.to_date(filters.get("date")) or self._dashboard_anchor_date(
                companies
            )
            preset = filters.get("period") or "today"
            end = anchor
            if preset == "yesterday":
                start = end = anchor - timedelta(days=1)
            elif preset == "last7":
                start = anchor - timedelta(days=6)
            elif preset == "month":
                start = anchor.replace(day=1)
            elif preset == "custom":
                start = fields.Date.to_date(filters.get("date_from"))
                end = fields.Date.to_date(filters.get("date_to"))
            elif preset == "today":
                start = anchor
            else:
                raise ValueError("Unknown preset")
            if not start or not end or start > end or (end - start).days > 365:
                raise ValueError("Invalid range")
        except (TypeError, ValueError, OverflowError) as error:
            raise ValidationError(_("Choose a valid date range of at most 366 days.")) from error
        return start, end

    def _dashboard_order_domain(self, filters, companies):
        domain = [("company_id", "in", companies.ids)]
        for name in ("provider_id", "store_binding_id"):
            if filters.get(name):
                domain.append((name, "=", filters[name]))
        if filters.get("pos_config_id"):
            domain.append(("pos_config_id", "=", filters["pos_config_id"]))
        if filters.get("environment"):
            domain.append(("connection_id.environment", "=", filters["environment"]))
        if filters.get("channel"):
            domain += self._channel_domain(filters["channel"], companies)
        return domain

    def _dashboard_connection_domain(self, filters, companies):
        domain = [("company_id", "in", companies.ids)]
        for key in ("provider_id", "environment"):
            if filters.get(key):
                domain.append((key, "=", filters[key]))
        if filters.get("pos_config_id"):
            domain.append(("pos_config_ids", "in", [filters["pos_config_id"]]))
        if filters.get("store_binding_id"):
            domain.append(("store_binding_ids", "in", [filters["store_binding_id"]]))
        return domain

    def _dashboard_queue_domain(self, filters, companies):
        # A connection can serve several stores. Queues are intentionally shown
        # at connection scope (including events that have not been store-mapped).
        return [
            ("connection_id." + name, operator, value)
            for name, operator, value in self._dashboard_connection_domain(filters, companies)
        ] + [("state", "in", ["retry", "failed"])]

    def _dashboard_period_domain(self, filters, companies=None):
        start, end = self._dashboard_period(filters, companies)
        return self._business_range_domain(start, end)

    @api.model
    def get_dashboard_options(self):
        self._dashboard_access()
        companies = self.env.companies
        configs = (
            self.env["pos.config"]
            .with_context(active_test=False)
            .search([("company_id", "in", companies.ids)], order="name, id")
        )
        providers = self.env["bs.delivery.provider"].search(
            [("module_available", "=", True)], order="name, id"
        )
        return {
            "companies": [{"id": item.id, "name": item.name} for item in companies],
            "company_id": self.env.company.id,
            "providers": [{"id": item.id, "name": item.name} for item in providers],
            "channels": self._dashboard_channels(companies),
            "branches": [
                {"id": item.id, "name": item.name, "company_id": item.company_id.id}
                for item in configs
            ],
            "today": fields.Date.to_string(self._dashboard_anchor_date()),
        }

    def _dashboard_live_summary(self, filters, companies, base_domain):
        orders = self.env["bs.delivery.order"]
        live_states = ["received", "accepted", "preparing", "ready", "picked_up"]
        connections = self.env["bs.delivery.connection"].search(
            self._dashboard_connection_domain(filters, companies)
        )
        return {
            "connections": len(connections),
            "configured": len(connections.filtered(lambda item: item.state == "connected")),
            "paused": len(connections.filtered("is_paused")),
            "connection_errors": len(connections.filtered(lambda item: item.state == "error")),
            "awaiting_acceptance": orders.search_count(base_domain + [("state", "=", "received")]),
            "active_orders": orders.search_count(base_domain + [("state", "in", live_states)]),
            "platforms": self._dashboard_live_channels(
                orders, base_domain + [("state", "in", live_states)]
            ),
        }

    def _dashboard_live_channels(self, orders, domain):
        channels = {}
        for source, provider, count in orders._read_group(
            domain, ["external_source", "provider_id"], ["__count"]
        ):
            key = self._channel_key(source, provider)
            item = channels.setdefault(
                key,
                {
                    "id": key,
                    "key": key,
                    "name": self._channel_label(key, provider),
                    "color": self._channel_color(key),
                    "provider_id": provider.id if self._channel_is_provider(key, provider) else 0,
                    "count": 0,
                },
            )
            item["count"] += count
        return sorted(channels.values(), key=lambda item: (-item["count"], item["name"].casefold()))

    def _dashboard_durations(self, domain):
        orders = self.env["bs.delivery.order"]
        timestamp_fields = ["received_at", "accepted_at", "preparing_at", "ready_at"]
        orders.flush_model(timestamp_fields)
        # _search carries the ORM's access checks and record rules into the
        # aggregate. No sudo, no unbounded record materialisation, no raw input SQL.
        query = orders._search(domain)
        timestamps = query.select(
            *(SQL.identifier(orders._table, name) for name in timestamp_fields)
        )
        acceptance, preparation, acceptance_samples, preparation_samples = self.env.execute_query(
            SQL(
                """SELECT
                AVG(CASE WHEN accepted_at >= received_at THEN
                    EXTRACT(EPOCH FROM (accepted_at - received_at)) / 60.0 END),
                AVG(CASE WHEN ready_at >= preparing_at THEN
                    EXTRACT(EPOCH FROM (ready_at - preparing_at)) / 60.0 END),
                COUNT(CASE WHEN accepted_at >= received_at THEN 1 END),
                COUNT(CASE WHEN ready_at >= preparing_at THEN 1 END)
               FROM (%s) AS scoped_orders""",
                timestamps,
            )
        )[0]
        return {
            "acceptance_minutes": float(acceptance) if acceptance is not None else None,
            "preparation_minutes": float(preparation) if preparation is not None else None,
            "acceptance_samples": acceptance_samples,
            "preparation_samples": preparation_samples,
        }

    @api.model
    def get_dashboard_action(
        self, kind, filters=None, state=None, provider_id=None, channel=None, pos_config_id=None
    ):
        """Allowlisted, read-only drilldowns with the same scope as each metric."""
        filters, companies = self._dashboard_filters(filters)
        domain = self._dashboard_order_domain(filters, companies)
        model = "bs.delivery.order"
        title = _("Delivery Orders")
        period_domain = self._dashboard_period_domain(filters, companies)
        if provider_id:
            extra, _companies = self._dashboard_filters({**filters, "provider_id": provider_id})
            domain.append(("provider_id", "=", extra["provider_id"]))
        if channel:
            self._dashboard_filters({**filters, "channel": channel})
            domain += self._channel_domain(channel, companies)
        if pos_config_id:
            extra, _companies = self._dashboard_filters(
                {**filters, "pos_config_id": pos_config_id}
            )
            domain.append(("pos_config_id", "=", extra["pos_config_id"]))
        if kind in ("orders", "gmv", "state", "delivered"):
            domain += period_domain
            if kind == "gmv":
                domain.append(("state", "not in", ["cancelled", "rejected"]))
            elif kind == "delivered":
                domain.append(("state", "=", "delivered"))
            elif kind == "state":
                if state not in self.env[model]._fields["state"].get_values(self.env):
                    raise ValidationError(_("Invalid delivery state."))
                domain.append(("state", "=", state))
        elif kind == "awaiting":
            domain.append(("state", "=", "received"))
        elif kind == "active":
            domain.append(
                ("state", "in", ["received", "accepted", "preparing", "ready", "picked_up"])
            )
        elif kind == "late":
            domain += [("is_late", "=", True)]
        elif kind in ("webhook", "outbox"):
            if not self.env.user.has_group(
                "bs_pos_delivery_core.group_delivery_integration_manager"
            ):
                raise AccessError(_("Integration administrator access is required."))
            model = "bs.delivery.webhook.event" if kind == "webhook" else "bs.delivery.outbox"
            title = _("Integration Queue Issues")
            domain = self._dashboard_queue_domain(filters, companies)
        elif kind == "catalog":
            if not self.env.user.has_group(
                "bs_pos_delivery_core.group_delivery_integration_manager"
            ):
                raise AccessError(_("Integration administrator access is required."))
            return self.env["ir.actions.actions"]._for_xml_id(
                "bs_pos_delivery_core.action_bs_delivery_provider"
            )
        elif kind == "connections":
            if not self.env.user.has_group("bs_pos_delivery_core.group_delivery_manager"):
                raise AccessError(_("Delivery manager access is required."))
            model = "bs.delivery.connection"
            title = _("Delivery Platforms")
            domain = self._dashboard_connection_domain(filters, companies)
        else:
            raise ValidationError(_("Unknown dashboard shortcut."))
        self.env[model].check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": title,
            "res_model": model,
            "views": [(False, "list"), (False, "form")],
            "domain": domain,
            "target": "current",
            "context": {"create": False, "allowed_company_ids": companies.ids},
        }
