"""Branch-local availability and transactional outbound notifications."""

import uuid

from odoo import fields

from .exceptions import BlendAPIError

from .menu_notifications import BlendMenuNotificationService


class BlendStockAvailabilityService:
    def __init__(self, env):
        # Inventory changes must notify even when caused by an inbound order.
        context = dict(env.context, blend_inbound_sync=False)
        # A stock check must not deduplicate a general menu edit or another
        # inventory operation which happened to inherit its notification ID.
        context.pop("blend_menu_change_id", None)
        self.env = env(context=context)

    def stock_available(self, templates, config):
        """Available = on hand minus reserved, at the POS picking source.

        The menu/order contract identifies templates and creates orders using
        product_variant_id; use that same variant, not unrelated variant stock.
        Never inherit a caller's warehouse/date/lot filters or fall back to a
        global warehouse balance when the branch location is missing.
        """
        templates = templates.sudo()
        result = {product.id: not product.is_storable for product in templates}
        tracked = templates.filtered("is_storable")
        source = config.sudo().picking_type_id.default_location_src_id
        if not tracked or not source or source.usage not in ("internal", "view"):
            return result
        if source.company_id and source.company_id != config.company_id:
            return result
        variants = tracked.product_variant_id
        balances = {
            variant.id: quantity - reserved
            for variant, quantity, reserved in self.env["stock.quant"].sudo()._read_group([
                ("product_id", "in", variants.ids),
                ("company_id", "=", config.company_id.id),
                ("location_id", "child_of", source.id),
                ("location_id.usage", "=", "internal"),
            ], ["product_id"], ["quantity:sum", "reserved_quantity:sum"])
        }
        for product in tracked:
            variant = product.product_variant_id
            result[product.id] = bool(
                variant and variant.uom_id.compare(balances.get(variant.id, 0.0), 0.0) > 0
            )
        return result

    def capture(self, pairs):
        """Snapshot effective availability BEFORE changing quants, in batches.

        Persist a check even when this transaction alone does not cross zero:
        concurrent changes to different lots can jointly exhaust the stock.
        The committed outbox worker evaluates the final balance before sending.
        """
        from .pos_api import BlendPOSAPIService

        if not pairs:
            return []
        configs = self.env["pos.config"].sudo().search([
            ("active", "=", True), ("blend_enabled", "=", True),
            "|", ("blend_event_url", "!=", False), ("blend_outbound_webhook_url", "!=", False),
        ], order="id")
        if not configs:
            return []
        variants = self.env["product.product"].sudo().browse(
            sorted({product_id for product_id, _location_id in pairs if product_id})
        ).exists()
        templates = variants.product_tmpl_id.filtered("is_storable")
        if not templates:
            return []
        locations = self.env["stock.location"].sudo().browse(
            sorted({location_id for _product_id, location_id in pairs if location_id})
        ).exists()
        location_by_id = {location.id: location for location in locations}
        ancestors = {
            location.id: {int(part) for part in location.parent_path.split("/") if part}
            for location in locations
        }
        parents = self.env["product.template"].sudo().search([
            ("modifier_grp_ids.modifier_ids", "in", templates.ids),
        ])
        snapshots = []
        for config in configs:
            source = config.picking_type_id.default_location_src_id
            if not source:
                continue
            affected_variant_ids = {
                product_id for product_id, location_id in pairs
                if location_id in location_by_id
                and source.id in ancestors[location_id]
                and location_by_id[location_id].usage == "internal"
                and location_by_id[location_id].company_id == config.company_id
            }
            affected = templates.filtered(lambda product: product.product_variant_id.id in affected_variant_ids)
            if not affected:
                continue
            menu_products = templates._blend_menu_products(
                config, [("id", "in", (affected | parents).ids)],
            )
            api = BlendPOSAPIService(self.env, config)
            api._stock_availability[config.id] = self.stock_available(affected, config)
            before = {str(product.id): api._product_available(product, config) for product in affected}
            for product in menu_products:
                groups = product.modifier_grp_ids.filtered(
                    lambda group: not group.blend_config_id or group.blend_config_id == config,
                )
                dependencies = (product | groups.modifier_ids) & affected
                if dependencies:
                    target = (config.id, "product", BlendMenuNotificationService.external_id(product))
                    snapshots.append((target, {str(item.id): before[str(item.id)] for item in dependencies}))
        return snapshots

    def enqueue(self, snapshots):
        notifier = BlendMenuNotificationService(self.env)
        for target, before in snapshots:
            config = self.env["pos.config"].sudo().browse(target[0])
            if not config.blend_outbound_webhook_url:
                notifier.enqueue({target}, stock_check=before)
                continue
            products = self.env["product.template"].sudo().with_context(active_test=False).browse(
                [int(product_id) for product_id in before]
            ).exists()
            own = products.filtered(lambda product: not product.use_as_modifier and
                                    BlendMenuNotificationService.external_id(product) == target[2])
            for product in own:
                self.env["blend.integration.log"].enqueue(
                    config, "item.updated", {
                        "brand_id": config.blend_brand_id or "",
                        "event_time": fields.Datetime.to_string(fields.Datetime.now()),
                        "event": "item.updated", "branch_id": config.blend_branch_id,
                        "product_id": target[2], "is_stock": before[str(product.id)],
                    },
                    idempotency_key="item:%s:%s:%s" % (uuid.uuid4().hex, config.id, product.id),
                    external_reference=target[2], stock_check={str(product.id): before[str(product.id)]},
                )
            dependencies = {key: value for key, value in before.items() if int(key) not in own.ids}
            if dependencies:
                # The published contract has no modifier.updated event.
                notifier.enqueue({target}, stock_check=dependencies)

    def capture_products(self, products, configs=None):
        """Capture manual availability changes using the same catalogue scope."""
        from .pos_api import BlendPOSAPIService

        if not products:
            return []
        products = products.sudo()
        configs = configs if configs is not None else self.env["pos.config"].sudo().search([
            ("blend_enabled", "=", True), ("blend_outbound_webhook_url", "!=", False),
        ])
        parents = self.env["product.template"].sudo().search([
            ("modifier_grp_ids.modifier_ids", "in", products.ids),
        ])
        snapshots = []
        for config in configs:
            menu = products._blend_menu_products(config, [("id", "in", (products | parents).ids)])
            api = BlendPOSAPIService(self.env, config)
            api._stock_availability[config.id] = self.stock_available(products, config)
            for product in menu:
                groups = product.modifier_grp_ids.filtered(lambda group: not group.blend_config_id or group.blend_config_id == config)
                dependencies = (product | groups.modifier_ids) & products
                if dependencies:
                    snapshots.append(((config.id, "product", BlendMenuNotificationService.external_id(product)), {
                        str(item.id): api._product_available(item, config) for item in dependencies
                    }))
        return snapshots

    def enqueue_branch(self, config, before):
        self.env["blend.integration.log"].enqueue(
            config, "store.updated", {
                "brand_id": config.blend_brand_id or "",
                "event_time": fields.Datetime.to_string(fields.Datetime.now()),
                "event": "store.updated", "branch_id": config.blend_branch_id,
                "status": config._blend_effective_available(),
            },
            idempotency_key="store:%s:%s" % (uuid.uuid4().hex, config.id),
            external_reference=config.blend_branch_id, stock_check={"store": before},
        )

    def prepare_delivery(self, log):
        """Compare final stock to the last delivery, not just this writer's view.

        A concurrent restock may have been delivered since the stock writer's
        snapshot. A persistent target state avoids suppressing the subsequent
        depletion notification in that case. Each parent of a modifier has its
        own state, so notifying one parent cannot suppress the others.
        """
        from .pos_api import BlendPOSAPIService

        config = log.config_id
        if not config.active or not config.blend_enabled or not (config.blend_outbound_webhook_url or config.blend_event_url):
            return None, {}, False
        state = self.env["blend.stock.state"].sudo()._lock_for_log(log)
        if log.event == "store.updated":
            current = {"store": config._blend_effective_available()}
            previous = {**log.stock_check, **(state.availability or {})}
            log.request_payload = {**log.request_payload, "status": current["store"],
                                   "event_time": fields.Datetime.to_string(fields.Datetime.now())}
            return state, current, current != previous
        products = self.env["product.template"].sudo().with_context(active_test=False).browse(
            [int(product_id) for product_id in log.stock_check]
        ).exists()
        api = BlendPOSAPIService(self.env, config)
        if log.event == "item.updated":
            identity_map = api._product_identity_map(api._products_for_config(config))
            target = identity_map.get(api._canonical_id(log.external_reference))
            original_ids = {int(product_id) for product_id in log.stock_check}
            if target and target.id not in original_ids:
                raise BlendAPIError("Product ID was reassigned; review the queued availability event.", 409)
            available = bool(target and api._product_available(target, config))
            current = {product_id: available for product_id in log.stock_check}
            previous = {**log.stock_check, **(state.availability or {})}
            log.request_payload = {**log.request_payload, "is_stock": available,
                                   "event_time": fields.Datetime.to_string(fields.Datetime.now())}
            return state, current, any(value != previous[key] for key, value in current.items())
        api._stock_availability[config.id] = self.stock_available(products, config)
        current = {str(product.id): api._product_available(product, config) for product in products}
        previous = {**log.stock_check, **(state.availability or {})}
        changed = any(value != previous[product_id] for product_id, value in current.items())
        return state, {**previous, **current}, changed
