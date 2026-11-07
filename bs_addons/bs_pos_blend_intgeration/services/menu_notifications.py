"""Transactional invalidations for the catalogue exposed by the POS API."""

import hashlib
import json
import uuid

from odoo import fields

from .exceptions import BlendOutboundError


class BlendMenuNotificationService:
    @staticmethod
    def external_id(record):
        value = str(record.blend_external_id or record.id).strip()
        return str(int(value)) if value.isdecimal() else value

    def __init__(self, env):
        self.env = env
        self.change_id = env.context.get("blend_menu_change_id") or uuid.uuid4().hex

    def destination_key(self, config):
        """Exact delivery scope; never expose credentials in a log or payload."""
        endpoint, method = self.env["blend.integration.log"]._event_endpoint(config, "menu.updated")
        scope = [config.company_id.id, config.blend_brand_id or "", endpoint or "", method,
                 config.blend_outbound_token or ""]
        return hashlib.sha256(json.dumps(scope, separators=(",", ":")).encode()).hexdigest()

    def delivery_configs(self, log, *, refresh=False):
        """Keep every participating branch's validation, not just the sender's.

        A changed destination/authentication must be reviewed, not silently
        adopted by a queued brand-level notification.
        """
        if not log.menu_scope:
            return log.config_id
        ids = log.menu_scope.get("config_ids", [])
        configs = self.env["pos.config"].sudo().with_context(active_test=False).browse(ids).exists()
        if (not configs or set(configs.ids) != set(ids) or log.config_id not in configs
                or any(not config.active or not config.blend_enabled for config in configs)):
            raise BlendOutboundError("A shared menu branch was removed or disabled. Review the affected branches before retrying.")
        if any(config.company_id != log.company_id or
               config.blend_brand_id != log.request_payload.get("brand_id") for config in configs):
            raise BlendOutboundError("A shared menu branch company or brand changed. The queued notification cannot be reassigned.")
        keys = {self.destination_key(config) for config in configs}
        if len(keys) != 1:
            raise BlendOutboundError("Shared menu branches no longer use the same webhook and authorization. Align their settings or create a fresh menu change.")
        endpoint, _method = log._event_endpoint(log.config_id, "menu.updated")
        if not endpoint:
            raise BlendOutboundError("The shared menu webhook is no longer configured.")
        if not refresh and (keys != {log.menu_scope.get("destination_key")} or endpoint != log.endpoint_url):
            raise BlendOutboundError("Shared menu destination or authorization changed. Review all branches, then use Reset and Retry.")
        return configs

    def retry_values(self, log):
        configs = self.delivery_configs(log, refresh=True)
        endpoint, method = log._event_endpoint(log.config_id, "menu.updated")
        return {
            "endpoint_url": endpoint, "http_method": method,
            "menu_scope": {"config_ids": sorted(configs.ids), "destination_key": self.destination_key(log.config_id)},
        }

    def snapshot(self, records):
        """Keep scalar targets so removals and reassignment retain the old scope.

        Reads deliberately use the API's privileged catalogue scope: a product
        editor need not have access to every configured branch. The originating
        create/write/unlink still runs with the editor's own access rights.
        """
        if not records or self.env.context.get("blend_inbound_sync"):
            return set()
        configs = self.env["pos.config"].sudo().search([
            ("blend_enabled", "=", True),
            "|", ("blend_event_url", "!=", False), ("blend_outbound_webhook_url", "!=", False),
        ])
        if not configs:
            return set()
        records = records.sudo().with_context(active_test=False)
        products = self.env["product.template"].sudo().with_context(active_test=False)
        if records._name == "product.template":
            parents = products.search([("modifier_grp_ids.modifier_ids", "in", records.ids)])
            candidates = records | parents
        elif records._name == "pos.category":
            candidates = products.search([("pos_categ_ids", "in", records.ids)])
        else:
            candidates = products.search([("modifier_grp_ids", "in", records.ids)])
        if not candidates:
            return set()

        targets = set()
        for config in configs:
            visible = products._blend_menu_products(config, [("id", "in", candidates.ids)])
            for product in visible:
                if records._name == "product.template":
                    groups = product.modifier_grp_ids.filtered(
                        lambda group: not group.blend_config_id or group.blend_config_id == config,
                    )
                    # Option edits invalidate the products whose menu embeds them.
                    if product in records or groups.modifier_ids & records:
                        targets.add((config.id, "product", self.external_id(product)))
                elif records._name == "pos.category":
                    categories = (product.pos_categ_ids & records).filtered(
                        lambda category: not category.blend_config_id or category.blend_config_id == config,
                    )
                    if config.limit_categories:
                        categories &= config.iface_available_categ_ids
                    targets.update(
                        (config.id, "category", self.external_id(category))
                        for category in categories
                    )
                else:
                    groups = (product.modifier_grp_ids & records).filtered(
                        lambda group: not group.blend_config_id or group.blend_config_id == config,
                    )
                    targets.update(
                        (config.id, "modifier_group", self.external_id(group))
                        for group in groups
                    )
        return targets

    def enqueue(self, targets, *, stock_check=None):
        if not targets or self.env.context.get("blend_inbound_sync"):
            return
        configs = self.env["pos.config"].sudo().browse(sorted({target[0] for target in targets}))
        config_by_id = {config.id: config for config in configs}
        event_time = fields.Datetime.to_string(fields.Datetime.now())
        groups = {}
        for config_id, entity_type, external_id in sorted(targets):
            config = config_by_id[config_id]
            # Stock checks carry a branch-specific baseline and must NEVER be
            # merged into a catalogue edit or another branch's stock check.
            shared = stock_check is None and bool(config.blend_brand_id)
            destination = self.destination_key(config) if shared else str(config_id)
            group = groups.setdefault((shared, destination, entity_type, external_id), [])
            group.append(config_id)
        for (shared, destination, entity_type, external_id), config_ids in groups.items():
            config = config_by_id[min(config_ids)]
            scope = {"config_ids": sorted(config_ids), "destination_key": destination} if shared else None
            self.env["blend.integration.log"].enqueue(
                config,
                "menu.updated",
                {
                    "brand_id": config.blend_brand_id or "",
                    "event_time": event_time,
                    "event": "menu.updated",
                    "entity": {"type": entity_type, "id": external_id},
                },
                # Separate saves within one second must not overwrite each other.
                # Nested hooks in the same operation may safely deduplicate.
                idempotency_key="%s:%s:%s:%s:%s" % (
                    "menu-shared" if shared else "menu", self.change_id,
                    destination, entity_type, external_id,
                ),
                external_reference=external_id,
                stock_check=stock_check,
                menu_scope=scope,
            )
