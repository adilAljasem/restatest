import logging

from odoo import Command


_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Register Blend and copy live legacy state before the old addon is removed."""

    provider = env.ref("bs_pos_delivery_core.provider_blend")
    env["bs.delivery.provider"]._register_compatibility_xmlids(provider)
    connections = _migrate_legacy_connections(env, provider)
    _migrate_legacy_orders(env, connections)
    _migrate_legacy_logs(env, connections)


def _columns(env, table):
    env.cr.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s",
        [table],
    )
    return {row[0] for row in env.cr.fetchall()}


def _migrate_legacy_connections(env, provider):
    required = {
        "id",
        "company_id",
        "blend_enabled",
        "blend_branch_id",
        "blend_brand_id",
        "blend_inbound_token",
        "blend_outbound_token",
        "blend_order_url",
        "blend_status_url",
        "blend_menu_url",
        "blend_event_url",
        "blend_schema",
        "blend_timeout",
        "blend_retry_max",
        "blend_sync_images",
    }
    if not required.issubset(_columns(env, "pos_config")):
        return {}
    env.cr.execute(
        "SELECT id, company_id, blend_branch_id, blend_brand_id, "
        "blend_inbound_token, blend_outbound_token, blend_order_url, "
        "blend_status_url, blend_menu_url, blend_event_url, blend_schema, "
        "blend_timeout, blend_retry_max, blend_sync_images "
        "FROM pos_config WHERE blend_enabled IS TRUE"
    )
    migrated = {}
    Connection = env["bs.delivery.connection"].sudo().with_context(active_test=False)
    Store = env["bs.delivery.store.binding"].sudo().with_context(active_test=False)
    for row in env.cr.fetchall():
        (
            config_id,
            company_id,
            branch_id,
            brand_id,
            inbound_token,
            outbound_token,
            order_url,
            status_url,
            menu_url,
            event_url,
            schema,
            timeout,
            retry_max,
            sync_images,
        ) = row
        config = env["pos.config"].sudo().browse(config_id).exists()
        if not config:
            continue
        if status_url and event_url and status_url.strip() != event_url.strip():
            raise ValueError(
                "Blend POS config %s has different status and event webhook URLs; "
                "configure one outbound webhook URL before migration." % config_id
            )
        connection = Connection.search(
            [
                ("provider_id", "=", provider.id),
                ("company_id", "=", company_id),
                ("pos_config_ids", "in", config_id),
            ],
            limit=1,
        )
        values = {
            "name": "Blend - %s" % config.display_name,
            "provider_id": provider.id,
            "company_id": company_id,
            "pos_config_ids": [Command.set([config_id])],
            "environment": "production",
            "state": "connected" if inbound_token and branch_id else "draft",
            "active": True,
            "auth_scheme": "bearer",
            "webhook_secret": inbound_token or False,
            "access_token": outbound_token or False,
            "blend_brand_id": brand_id or False,
            "blend_order_url": order_url or False,
            "blend_outbound_webhook_url": status_url or event_url or False,
            "blend_menu_url": menu_url or False,
            "blend_schema": "pos",
            "blend_timeout_seconds": min(max(timeout or 10, 1), 120),
            "blend_sync_images": True if sync_images is None else bool(sync_images),
            "outgoing_mode": "queued",
            "outbox_max_attempts": min(max(retry_max or 5, 1), 10),
        }
        if connection:
            values.pop("name", None)
            values.pop("provider_id", None)
            values.pop("company_id", None)
            values.pop("pos_config_ids", None)
            # Never replace credentials already entered in the new connection.
            if connection.webhook_secret:
                values.pop("webhook_secret", None)
            if connection.access_token:
                values.pop("access_token", None)
            connection.write(values)
        else:
            connection = Connection.create(values)
        config.with_context(bs_blend_store_sync=True).write(
            {
                "blend_enabled": True,
                "blend_branch_id": branch_id,
                "blend_brand_id": brand_id,
            }
        )
        store = Store.search(
            [
                ("connection_id", "=", connection.id),
                ("pos_config_id", "=", config.id),
            ],
            limit=1,
        )
        store_values = {
            "remote_store_id": str(branch_id or "odoo-pos-%s" % config.id),
            "remote_vendor_id": str(brand_id or "") or False,
            "active": True,
        }
        if store:
            store.write(store_values)
        else:
            Store.create(
                {
                    "connection_id": connection.id,
                    "pos_config_id": config.id,
                    **store_values,
                }
            )
        migrated[config.id] = connection
    if migrated:
        _logger.info("Migrated %s legacy Blend POS configuration(s)", len(migrated))
    return migrated


def _migrate_legacy_orders(env, connections):
    if not connections or "blend_order_id" not in _columns(env, "pos_order"):
        return
    orders = env["pos.order"].sudo().search(
        [
            ("config_id", "in", list(connections)),
            ("blend_order_id", "!=", False),
        ]
    )
    bridge = env["bs.delivery.blend.bridge"]
    for order in orders:
        bridge.ensure_order_binding(order, connections[order.config_id.id])
    if orders:
        _logger.info("Migrated %s legacy Blend order binding(s)", len(orders))


def _migrate_legacy_logs(env, connections):
    columns = _columns(env, "blend_integration_log")
    required = {
        "id",
        "direction",
        "event",
        "state",
        "config_id",
        "order_id",
        "external_reference",
        "endpoint_url",
        "http_method",
        "request_payload",
        "response_payload",
        "http_status",
        "attempt_count",
        "duration_ms",
        "error_message",
    }
    if not connections or not required.issubset(columns):
        return
    env.cr.execute(
        "SELECT id, direction, event, state, config_id, order_id, "
        "external_reference, endpoint_url, http_method, request_payload, "
        "response_payload, http_status, attempt_count, duration_ms, error_message "
        "FROM blend_integration_log WHERE config_id = ANY(%s)",
        [list(connections)],
    )
    Log = env["bs.delivery.sync.log"].sudo()
    Binding = env["bs.delivery.order.binding"].sudo()
    migrated = 0
    for row in env.cr.fetchall():
        (
            legacy_id,
            direction,
            event,
            state,
            config_id,
            order_id,
            external_reference,
            endpoint_url,
            http_method,
            request_payload,
            response_payload,
            http_status,
            attempt_count,
            duration_ms,
            error_message,
        ) = row
        correlation_id = "blend-legacy-log:%s" % legacy_id
        if Log.search_count([("correlation_id", "=", correlation_id)]):
            continue
        connection = connections.get(config_id)
        if not connection:
            continue
        binding = Binding.search(
            [
                ("connection_id", "=", connection.id),
                ("pos_order_id", "=", order_id),
            ],
            limit=1,
        ) if order_id else Binding
        status = (
            "success"
            if state in ("success", "duplicate")
            else "failed"
            if state == "failed"
            else "pending"
        )
        Log.create(
            {
                "connection_id": connection.id,
                "binding_id": binding.id if binding else False,
                "direction": "incoming" if direction == "inbound" else "outgoing",
                "operation": "legacy:%s" % event,
                "external_id": external_reference or False,
                "correlation_id": correlation_id,
                "request_url": endpoint_url or False,
                "request_method": str(http_method or "POST").upper(),
                "http_status": http_status or False,
                "duration_ms": duration_ms or 0.0,
                "attempt_number": max(attempt_count or 1, 1),
                "status": status,
                "error_message": error_message or False,
                "request_payload": request_payload,
                "response_payload": response_payload,
            }
        )
        migrated += 1
    if migrated:
        _logger.info("Copied %s legacy Blend log row(s) into Core audit logs", migrated)

