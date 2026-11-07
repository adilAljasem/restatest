"""Queue Blend catalogue invalidations in the product transaction."""

import hashlib
import json
import uuid

from .pos_api import BlendPOSAPIService


class BlendMenuNotificationService:
    def __init__(self, env):
        self.env = env
        self.change_id = env.context.get("blend_menu_change_id") or uuid.uuid4().hex

    @staticmethod
    def destination_key(connection):
        """Freeze the destination and authentication without storing the token."""
        scope = [
            connection.company_id.id,
            connection.blend_brand_id or "",
            connection.blend_outbound_webhook_url or "",
            connection.access_token or "",
        ]
        return hashlib.sha256(json.dumps(scope, separators=(",", ":")).encode()).hexdigest()

    def snapshot(self, records):
        if not records or self.env.context.get("blend_inbound_sync"):
            return set()

        connections = self.env["bs.delivery.connection"].sudo().search([
            ("provider_id.code", "=", "blend"),
            ("state", "=", "connected"),
            ("active", "=", True),
            ("blend_outbound_webhook_url", "!=", False),
        ])
        if not connections:
            return set()

        products = self.env["product.template"].sudo().with_context(active_test=False)
        changed = records.sudo().with_context(active_test=False)
        candidates = changed
        if "modifier_group_line_ids" in products._fields:
            candidates |= products.search([
                ("modifier_group_line_ids.modifier_group_id.option_ids.product_id.product_tmpl_id", "in", changed.ids),
            ])
        if "combo_ids" in products._fields:
            candidates |= products.search([
                ("combo_ids.combo_item_ids.product_id.product_tmpl_id", "in", changed.ids),
            ])
        targets = set()
        for connection in connections:
            relevant = candidates.filtered(
                lambda product: not product.company_id or product.company_id == connection.company_id,
            )
            if not relevant:
                continue
            for config in connection.pos_config_ids.filtered(lambda branch: branch.active and branch.blend_enabled):
                visible = products._blend_menu_products(config, [("id", "in", relevant.ids)])
                for product in visible:
                    targets.add((connection.id, "product", BlendPOSAPIService._record_external_id(product)))
        return targets

    def enqueue(self, targets):
        if not targets or self.env.context.get("blend_inbound_sync"):
            return
        versions = self.env["bs.delivery.menu.version"].sudo()
        connections = self.env["bs.delivery.connection"].sudo().browse(
            sorted({connection_id for connection_id, _entity_type, _entity_id in targets}),
        )
        connection_by_id = {connection.id: connection for connection in connections}
        existing = {
            (version.connection_id.id, version.blend_entity_type, version.blend_entity_id)
            for version in versions.search([
                ("connection_id", "in", connections.ids),
                ("blend_change_id", "=", self.change_id),
            ])
        }
        values_list = []
        for connection_id, entity_type, entity_id in sorted(targets):
            if (connection_id, entity_type, entity_id) in existing:
                continue
            values_list.append({
                "name": "Blend %s updated: %s" % (entity_type, entity_id),
                "connection_id": connection_id,
                "direction": "push",
                "state": "queued",
                "blend_change_id": self.change_id,
                "blend_entity_type": entity_type,
                "blend_entity_id": entity_id,
                "blend_destination_key": self.destination_key(connection_by_id[connection_id]),
            })
        if values_list:
            versions.create(values_list)
