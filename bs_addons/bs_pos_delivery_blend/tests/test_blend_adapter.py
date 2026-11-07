import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged

from ..services.exceptions import BlendAPIError
from ..services.webhook import BlendWebhookService
from odoo.addons.bs_pos_delivery_core.exceptions import DeliveryBusinessError


def sample_menu(branch_id, *, event_time="2026-09-01T10:00:00Z"):
    return {
        "event": "menu_sync",
        "event_time": event_time,
        "branch_id": branch_id,
        "payload": {
            "categories": [
                {
                    "id": "category-10",
                    "name": {"ar": "بيتزا", "en": "Pizza"},
                    "schedule_time": {},
                    "items": ["item-100"],
                }
            ],
            "items": [
                {
                    "id": "item-100",
                    "name": {"ar": "بيتزا مارجريتا", "en": "Margherita Pizza"},
                    "description": {"ar": "وصف", "en": "Description"},
                    "price": 150.0,
                    "is_available": True,
                    "categories": ["category-10"],
                    "images": [],
                    "schedule_time": {},
                }
            ],
            "toppings": [
                {
                    "id": "group-20",
                    "name": {"ar": "الإضافات", "en": "Add-ons"},
                    "is_required": True,
                    "min": 1,
                    "max": 2,
                    "max_per_quantity": 2,
                    "options": [
                        {
                            "id": "option-200",
                            "name": {"ar": "جبنة إضافية", "en": "Extra Cheese"},
                            "price": 10.0,
                        }
                    ],
                    "items": ["item-100"],
                }
            ],
            "branch_schedule_time": {},
        },
    }


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


@tagged("post_install", "-at_install")
class TestBlendAdapter(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        suffix = uuid.uuid4().hex[:10]
        cls.branch_id = "branch-%s" % suffix
        cls.brand_id = "brand-%s" % suffix
        cls.token = "inbound-%s" % suffix
        cls.outbound_token = "outbound-%s" % suffix
        cls.config = cls.env["pos.config"].create(
            {
                "name": "Blend Test POS %s" % suffix,
                "blend_branch_id": cls.branch_id,
                "blend_brand_id": cls.brand_id,
                "payment_method_ids": [(6, 0, [])],
            }
        )
        cls.connection = cls.env["bs.delivery.connection"].sudo().create(
            {
                "name": "Blend Test %s" % suffix,
                "provider_id": cls.env.ref("bs_pos_delivery_core.provider_blend").id,
                "company_id": cls.env.company.id,
                "pos_config_ids": [(6, 0, cls.config.ids)],
                "environment": "test",
                "state": "connected",
                "webhook_secret": cls.token,
                "access_token": cls.outbound_token,
                "blend_brand_id": cls.brand_id,
                "blend_order_url": "https://blend.example.test/orders",
                "blend_outbound_webhook_url": "https://blend.example.test/webhook",
                "blend_menu_url": "https://blend.example.test/menu",
                "blend_sync_images": False,
                "blend_timeout_seconds": 5,
                "outgoing_mode": "queued",
                "outbox_max_attempts": 3,
            }
        )
        cls.store = cls.connection._blend_store_for_config(cls.config)

    def test_store_activation_controls_blend_branch(self):
        self.assertTrue(self.config.blend_enabled)
        self.store.active = False
        self.config.invalidate_recordset(["blend_enabled"])
        self.assertFalse(self.config.blend_enabled)
        self.assertNotIn(self.config, self.connection.pos_config_ids)

        self.store.active = True
        self.config.invalidate_recordset(["blend_enabled"])
        self.assertTrue(self.config.blend_enabled)
        self.assertIn(self.config, self.connection.pos_config_ids)

    def test_store_mapping_updates_blend_branch_identity(self):
        branch_id = "updated-%s" % uuid.uuid4().hex[:10]
        brand_id = "brand-%s" % uuid.uuid4().hex[:10]
        self.store.write({
            "remote_store_id": branch_id,
            "remote_vendor_id": brand_id,
        })
        self.assertEqual(self.config.blend_branch_id, branch_id)
        self.assertEqual(self.config.blend_brand_id, brand_id)
        self.assertTrue(self.config.blend_enabled)

    def test_duplicate_active_blend_store_mapping_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.env["bs.delivery.store.binding"].sudo().create({
                "connection_id": self.connection.id,
                "pos_config_id": self.config.id,
                "remote_store_id": "duplicate-%s" % uuid.uuid4().hex[:10],
            })

    def webhook(self, payload):
        return BlendWebhookService(self.env).handle(
            payload,
            "Bearer %s" % self.token,
            request_headers={"X-Request-ID": "blend-test"},
        )

    def _create_order(self):
        self.webhook(sample_menu(self.branch_id))
        product = self.env["product.template"].search(
            [("blend_external_id", "=", "item-100")], limit=1
        )
        option = self.env["product.template"].search(
            [("blend_external_id", "=", "option-200")], limit=1
        )
        modifier_option = product.modifier_group_line_ids.modifier_group_id.option_ids.filtered(
            lambda record: record.product_id == option.product_variant_id
        )
        session = self.env["pos.session"].create({"config_id": self.config.id})
        partner = self.env["res.partner"].create(
            {"name": "Blend Customer", "phone": "+966500000000"}
        )
        order = self.env["pos.order"].create(
            {
                "session_id": session.id,
                "config_id": self.config.id,
                "uuid": str(uuid.uuid4()),
                "partner_id": partner.id,
                "amount_tax": 10.0,
                "amount_total": 160.0,
                "amount_paid": 160.0,
                "amount_return": 0.0,
            }
        )
        parent = self.env["pos.order.line"].create(
            {
                "order_id": order.id,
                "product_id": product.product_variant_id.id,
                "qty": 1,
                "price_unit": 150.0,
                "price_subtotal": 150.0,
                "price_subtotal_incl": 150.0,
            }
        )
        self.env["pos.order.line"].create(
            {
                "order_id": order.id,
                "product_id": option.product_variant_id.id,
                "qty": 1,
                "price_unit": 10.0,
                "price_subtotal": 10.0,
                "price_subtotal_incl": 10.0,
                "is_modifier_line": True,
                "modifier_parent_line_id": parent.id,
                "modifier_option_id": modifier_option.id,
                "modifier_option_name": modifier_option.name,
                "modifier_qty_per_parent": 1,
            }
        )
        return order

    def test_legacy_menu_contract_is_idempotent_and_uses_core_logs(self):
        result = self.webhook(sample_menu(self.branch_id))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["processed_branches"], 1)
        self.assertEqual(
            result["result"],
            {"categories": 1, "items": 1, "toppings": 1, "options": 1},
        )
        duplicate = self.webhook(sample_menu(self.branch_id))
        self.assertEqual(duplicate["processed_branches"], 0)
        self.assertEqual(duplicate["duplicate_branches"], 1)
        event = self.env["bs.delivery.webhook.event"].search(
            [("connection_id", "=", self.connection.id), ("event_type", "=", "menu_sync")]
        )
        self.assertEqual(len(event), 1)
        self.assertEqual(event.state, "done")
        self.assertTrue(
            self.env["bs.delivery.sync.log"].search_count(
                [("webhook_event_id", "=", event.id), ("status", "=", "success")]
            )
        )
        binding = self.env["bs.delivery.product.binding"].search(
            [
                ("connection_id", "=", self.connection.id),
                ("external_product_id", "=", "item-100"),
            ]
        )
        self.assertTrue(binding.product_id)
        product = binding.product_id.product_tmpl_id
        self.assertEqual(product.type, "consu")
        group_lines = product.modifier_group_line_ids.filtered(
            lambda line: line.modifier_group_id.blend_external_id == "group-20"
        )
        self.assertTrue(product.allow_modifiers)
        self.assertEqual(len(group_lines), 1)
        group = group_lines.modifier_group_id
        self.assertEqual(group.max_qty, 2)
        self.assertEqual(group.min_qty, 1)
        self.assertEqual(group.option_ids.blend_external_id, "option-200")
        self.assertEqual(group.option_ids.price_mode, "fixed_extra")
        self.assertEqual(group.option_ids.extra_price, 10.0)
        self.assertEqual(group.option_ids.max_qty, 2)

    def test_reused_event_id_with_changed_payload_is_conflict(self):
        first = sample_menu(self.branch_id)
        first["event_id"] = "blend-menu-replay-1"
        self.webhook(first)
        self.assertEqual(self.webhook(first)["duplicate_branches"], 1)
        changed = sample_menu(self.branch_id)
        changed["event_id"] = first["event_id"]
        changed["payload"]["items"][0]["price"] = 175.0
        with self.assertRaises(BlendAPIError) as raised:
            self.webhook(changed)
        self.assertEqual(raised.exception.status_code, 409)
        events = self.env["bs.delivery.webhook.event"].search([
            ("connection_id", "=", self.connection.id),
            ("event_id", "=", first["event_id"]),
        ])
        self.assertEqual(len(events), 1)
        self.assertEqual(events.state, "done")

    def test_platform_pull_menu_uses_explicit_contract_and_records_result(self):
        adapter = self.connection._get_adapter()
        self.assertTrue(adapter.supports("pull_menu"))
        self.assertTrue(adapter.supports("push_menu"))
        self.assertTrue(adapter.supports("update_product_availability"))
        self.assertTrue(adapter.supports("sync_products"))
        self.assertFalse(adapter.supports("sync_menu"))
        self.assertTrue(self.connection.can_pull_menu)
        self.assertTrue(self.connection.can_push_menu)

        response = FakeResponse(200, sample_menu(self.branch_id))
        with patch(
            "odoo.addons.bs_pos_delivery_blend.models.delivery_adapter.requests.get",
            return_value=response,
        ) as request_get:
            action = self.connection.action_pull_menu()

        version = self.env["bs.delivery.menu.version"].browse(action["res_id"])
        self.assertEqual(version.direction, "pull")
        self.assertEqual(version.store_binding_id, self.store)
        self.assertEqual(version.state, "done")
        self.assertFalse(version.last_error)
        request_get.assert_called_once()
        self.assertEqual(request_get.call_args.args[0], self.connection.blend_menu_url)

    def test_platform_push_menu_triggers_webhook_and_logs(self):
        response = FakeResponse(200, {"success": True})
        with patch(
            "odoo.addons.bs_pos_delivery_blend.models.delivery_adapter.requests.post",
            return_value=response,
        ) as request_post:
            action = self.connection.action_push_menu()

        version = self.env["bs.delivery.menu.version"].browse(action["res_id"])
        self.assertEqual(version.direction, "push")
        self.assertEqual(version.store_binding_id, self.store)
        self.assertEqual(version.state, "done")
        self.assertFalse(version.last_error)

        request_post.assert_called_once()
        called_url = request_post.call_args[0][0]
        called_kwargs = request_post.call_args[1]
        self.assertEqual(called_url, self.connection.blend_outbound_webhook_url)
        self.assertEqual(called_kwargs["json"]["event"], "menu.updated")
        self.assertEqual(called_kwargs["json"]["entity"]["type"], "product")

        log = self.env["bs.delivery.sync.log"].search([
            ("connection_id", "=", self.connection.id),
            ("operation", "=", "blend_menu_push"),
        ], limit=1)
        self.assertTrue(log)
        self.assertEqual(log.status, "success")
        self.assertEqual(log.direction, "outgoing")

    def test_outbound_webhook_url_is_required_for_blend_connection(self):
        self.connection.blend_outbound_webhook_url = False
        with self.assertRaisesRegex(DeliveryBusinessError, "Blend Outbound Webhook URL"):
            self.connection._blend_validate_configuration()

    def test_product_price_edit_queues_blend_menu_notification(self):
        product = self.env["product.template"].with_context(
            blend_inbound_sync=True
        ).create({
            "name": "Native Blend Product",
            "available_in_pos": True,
            "company_id": self.env.company.id,
        }).with_env(self.env)
        self.assertIn(product, self.env["product.template"]._blend_menu_products(self.config))
        last_job = self.env["bs.delivery.menu.version"].search([], order="id desc", limit=1).id or 0
        with patch(
            "odoo.addons.bs_pos_delivery_blend.models.delivery_adapter.requests.post"
        ) as request_post:
            product.write({"list_price": 41.0})
            request_post.assert_not_called()

        jobs = self.env["bs.delivery.menu.version"].search([
            ("id", ">", last_job),
            ("connection_id", "=", self.connection.id),
            ("direction", "=", "push"),
        ])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs.state, "queued")
        self.assertEqual(jobs.blend_entity_id, str(product.id))

        with patch(
            "odoo.addons.bs_pos_delivery_blend.models.delivery_adapter.requests.post",
            return_value=FakeResponse(200, {"success": True}),
        ) as request_post:
            self.env["bs.delivery.menu.version"]._cron_process_blend_menu_notifications()
        request_post.assert_called_once()
        self.assertEqual(request_post.call_args.kwargs["json"]["entity"], {
            "type": "product", "id": str(product.id),
        })
        self.assertEqual(jobs.state, "done")

    def test_blend_menu_notification_ignores_inbound_and_unrelated_edits(self):
        product = self.env["product.template"].with_context(
            blend_inbound_sync=True
        ).create({
            "name": "Inbound Blend Product",
            "available_in_pos": True,
            "company_id": self.env.company.id,
        }).with_env(self.env)
        last_job = self.env["bs.delivery.menu.version"].search([], order="id desc", limit=1).id or 0
        product.with_context(blend_inbound_sync=True).write({"list_price": 25.0})
        product.write({"barcode": "BLEND-UNRELATED-%s" % product.id})
        self.assertFalse(self.env["bs.delivery.menu.version"].search([
            ("id", ">", last_job),
            ("blend_change_id", "!=", False),
        ]))

    def test_blend_menu_notification_retains_previous_product_id(self):
        product = self.env["product.template"].with_context(
            blend_inbound_sync=True
        ).create({
            "name": "Blend Renamed ID",
            "available_in_pos": True,
            "company_id": self.env.company.id,
            "blend_external_id": "123456",
        }).with_env(self.env)
        last_job = self.env["bs.delivery.menu.version"].search([], order="id desc", limit=1).id or 0
        product.write({"blend_external_id": "654321"})
        jobs = self.env["bs.delivery.menu.version"].search([
            ("id", ">", last_job),
            ("connection_id", "=", self.connection.id),
            ("blend_change_id", "!=", False),
        ])
        self.assertEqual(set(jobs.mapped("blend_entity_id")), {"123456", "654321"})

    def test_blend_menu_notification_queues_create_and_archive(self):
        jobs_model = self.env["bs.delivery.menu.version"]
        last_job = jobs_model.search([], order="id desc", limit=1).id or 0
        product = self.env["product.template"].create({
            "name": "Created Blend Product",
            "available_in_pos": True,
            "company_id": self.env.company.id,
        })
        created = jobs_model.search([
            ("id", ">", last_job),
            ("connection_id", "=", self.connection.id),
            ("blend_entity_id", "=", str(product.id)),
        ])
        self.assertEqual(len(created), 1)
        product.active = False
        archived = jobs_model.search([
            ("id", ">", created.id),
            ("connection_id", "=", self.connection.id),
            ("blend_entity_id", "=", created.blend_entity_id),
        ])
        self.assertEqual(len(archived), 1)

    def test_blend_menu_notification_rejects_changed_destination(self):
        product = self.env["product.template"].with_context(
            blend_inbound_sync=True
        ).create({
            "name": "Destination Guard Product",
            "available_in_pos": True,
            "company_id": self.env.company.id,
        }).with_env(self.env)
        product.write({"list_price": 19.0})
        job = self.env["bs.delivery.menu.version"].search([
            ("connection_id", "=", self.connection.id),
            ("blend_entity_id", "=", str(product.id)),
            ("state", "=", "queued"),
        ], order="id desc", limit=1)
        self.assertTrue(job)
        self.connection.blend_outbound_webhook_url = "https://blend.example.test/different-events"
        with patch(
            "odoo.addons.bs_pos_delivery_blend.models.delivery_adapter.requests.post"
        ) as request_post:
            self.env["bs.delivery.menu.version"]._cron_process_blend_menu_notifications()
            request_post.assert_not_called()
        self.assertEqual(job.state, "failed")

    def test_blend_menu_notification_retries_failed_delivery(self):
        product = self.env["product.template"].with_context(
            blend_inbound_sync=True
        ).create({
            "name": "Retry Blend Product",
            "available_in_pos": True,
            "company_id": self.env.company.id,
        }).with_env(self.env)
        product.write({"list_price": 21.0})
        job = self.env["bs.delivery.menu.version"].search([
            ("connection_id", "=", self.connection.id),
            ("blend_entity_id", "=", str(product.id)),
            ("state", "=", "queued"),
        ], order="id desc", limit=1)
        with patch(
            "odoo.addons.bs_pos_delivery_blend.models.delivery_adapter.requests.post",
            return_value=FakeResponse(503, {"error": "temporary"}),
        ):
            self.env["bs.delivery.menu.version"]._cron_process_blend_menu_notifications()
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.blend_attempt_count, 1)
        job.blend_next_retry_at = fields.Datetime.now() - timedelta(minutes=1)
        with patch(
            "odoo.addons.bs_pos_delivery_blend.models.delivery_adapter.requests.post",
            return_value=FakeResponse(200, {"success": True}),
        ) as request_post:
            self.env["bs.delivery.menu.version"]._cron_process_blend_menu_notifications()
        request_post.assert_called_once()
        self.assertEqual(job.state, "done")

    def test_blend_menu_notification_recovers_interrupted_processing(self):
        product = self.env["product.template"].with_context(
            blend_inbound_sync=True
        ).create({
            "name": "Interrupted Blend Product",
            "available_in_pos": True,
            "company_id": self.env.company.id,
        }).with_env(self.env)
        product.write({"list_price": 22.0})
        job = self.env["bs.delivery.menu.version"].search([
            ("connection_id", "=", self.connection.id),
            ("blend_entity_id", "=", str(product.id)),
            ("state", "=", "queued"),
        ], order="id desc", limit=1)
        job.write({
            "state": "processing",
            "started_at": fields.Datetime.now() - timedelta(minutes=15),
        })
        with patch(
            "odoo.addons.bs_pos_delivery_blend.models.delivery_adapter.requests.post",
            return_value=FakeResponse(200, {"success": True}),
        ) as request_post:
            self.env["bs.delivery.menu.version"]._cron_process_blend_menu_notifications()
        request_post.assert_called_once()
        self.assertEqual(job.state, "done")

    def test_product_template_toggle_availability_and_sync(self):
        self.webhook(sample_menu(self.branch_id))
        product = self.env["product.template"].search(
            [("blend_external_id", "=", "item-100")], limit=1
        )
        self.assertTrue(product)
        self.assertTrue(product.delivery_available)
        self.assertTrue(product.blend_is_available)

        response = FakeResponse(200, {"success": True})
        with patch(
            "odoo.addons.bs_pos_delivery_blend.models.delivery_adapter.requests.post",
            return_value=response,
        ) as request_post:
            product.action_delivery_toggle_availability()

        self.assertFalse(product.delivery_available)
        self.assertFalse(product.blend_is_available)
        request_post.assert_called_once()
        called_url = request_post.call_args[0][0]
        called_kwargs = request_post.call_args[1]
        self.assertEqual(called_url, self.connection.blend_outbound_webhook_url)
        self.assertEqual(called_kwargs["json"]["event"], "item.updated")
        self.assertEqual(called_kwargs["json"]["product_id"], "item-100")
        self.assertFalse(called_kwargs["json"]["is_stock"])
        self.assertFalse(self.env["bs.delivery.menu.version"].search([
            ("connection_id", "=", self.connection.id),
            ("blend_change_id", "!=", False),
        ]))

        log = self.env["bs.delivery.sync.log"].search([
            ("connection_id", "=", self.connection.id),
            ("operation", "=", "blend_item_availability"),
        ], limit=1)
        self.assertTrue(log)
        self.assertEqual(log.status, "success")

        # Toggle back to available
        with patch(
            "odoo.addons.bs_pos_delivery_blend.models.delivery_adapter.requests.post",
            return_value=response,
        ) as request_post_2:
            product.action_delivery_toggle_availability()

        self.assertTrue(product.delivery_available)
        self.assertTrue(product.blend_is_available)
        request_post_2.assert_called_once()
        self.assertTrue(request_post_2.call_args[1]["json"]["is_stock"])

        # Sync product to platform
        with patch(
            "odoo.addons.bs_pos_delivery_blend.models.delivery_adapter.requests.post",
            return_value=response,
        ) as request_sync:
            product.action_delivery_sync_product()

        request_sync.assert_called_once()
        self.assertEqual(request_sync.call_args[1]["json"]["event"], "menu.updated")
        self.assertEqual(request_sync.call_args[1]["json"]["entity"]["type"], "product")
        self.assertEqual(request_sync.call_args[1]["json"]["entity"]["id"], "item-100")

    def test_pos_api_checks_delivery_available(self):
        from ..services.pos_api import BlendPOSAPIService
        self.webhook(sample_menu(self.branch_id))
        product = self.env["product.template"].search(
            [("blend_external_id", "=", "item-100")], limit=1
        )
        service = BlendPOSAPIService(self.env, self.config)
        self.assertTrue(service._product_available(product, self.config))

        product.delivery_available = False
        self.assertFalse(service._product_available(product, self.config))

        product.delivery_available = True
        self.assertTrue(service._product_available(product, self.config))

    def test_pos_orders_cannot_be_exported_to_blend(self):
        order = self._create_order()
        adapter = self.connection._get_adapter()
        self.assertFalse(adapter.supports("send_order"))
        with self.assertRaises(ValidationError):
            order._blend_enqueue_order_created(connection=self.connection)
        with self.assertRaises(DeliveryBusinessError):
            adapter.send_order(self.connection, order)
        with self.assertRaises(ValidationError):
            order._blend_enqueue_status("accepted", connection=self.connection)
        self.assertFalse(self.env["bs.delivery.outbox"].search([
            ("connection_id", "=", self.connection.id),
            ("operation", "in", ("send_order", "update_order")),
        ]))

    def test_obsolete_send_order_job_cannot_block_later_status(self):
        order = self._create_order()
        order.with_context(bs_blend_bridge_sync=True).write({"blend_order_id": "old-blend-1"})
        binding = self.env["bs.delivery.blend.bridge"].ensure_order_binding(order, self.connection)
        Outbox = self.env["bs.delivery.outbox"].sudo()
        old_job = Outbox.create({
            "connection_id": self.connection.id,
            "binding_id": binding.id,
            "operation": "send_order",
            "idempotency_key": "old-send-%s" % order.id,
            "state": "failed",
        })
        status_job = Outbox.create({
            "connection_id": self.connection.id,
            "binding_id": binding.id,
            "operation": "update_order",
            "status_value": "accepted",
            "idempotency_key": "later-status-%s" % order.id,
            "priority": max(Outbox.search([]).mapped("priority") or [0]) + 1,
        })
        with patch.object(type(status_job), "_dispatch_operation", return_value={"success": True}):
            Outbox._process_due_jobs(batch_size=1)
        self.assertEqual(old_job.state, "cancelled")
        self.assertEqual(status_job.state, "done")
        self.assertEqual(old_job.attempt_count, 0)

    def test_legacy_pos_status_callback_requires_core_intake(self):
        order = self._create_order()
        order.with_context(bs_blend_bridge_sync=True).write({"blend_order_id": "legacy-status-1"})
        with self.assertRaises(BlendAPIError) as raised:
            self.webhook({
                "event": "order_updated", "event_time": "2026-09-01T10:30:00Z",
                "branch_id": self.branch_id,
                "payload": {"order_id": "legacy-status-1", "status": "prepared"},
            })
        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse(order.blend_status)

    def test_legacy_binding_callback_does_not_mutate_pos_or_core(self):
        order = self._create_order()
        order.with_context(bs_blend_bridge_sync=True).write(
            {"blend_order_id": "blend-status-1", "blend_status": "pending"}
        )
        binding = self.env["bs.delivery.blend.bridge"].ensure_order_binding(
            order, self.connection
        )
        with self.assertRaises(BlendAPIError) as raised:
            self.webhook({
                "event": "order_updated",
                "event_time": "2026-09-01T10:30:00Z",
                "branch_id": self.branch_id,
                "payload": {"order_id": "blend-status-1", "status": "prepared"},
            })
        self.assertEqual(raised.exception.status_code, 409)
        order.invalidate_recordset()
        binding.invalidate_recordset()
        canonical = self.env["bs.delivery.order"].search([("binding_id", "=", binding.id)])
        self.assertEqual(order.blend_status, "pending")
        self.assertEqual(binding.odoo_status, "pending")
        self.assertEqual(canonical.state, "received")

    def test_existing_legacy_binding_tracks_pos_status_without_new_order(self):
        order = self._create_order()
        order.with_context(bs_blend_bridge_sync=True).write({
            "blend_order_id": "legacy-existing-1",
            "blend_status": "pending",
        })
        bridge = self.env["bs.delivery.blend.bridge"]
        binding = bridge.ensure_order_binding(order, self.connection)
        canonical = self.env["bs.delivery.order"].search([
            ("binding_id", "=", binding.id),
        ])

        order.write({"blend_status": "rejected"})
        binding.invalidate_recordset()
        self.assertEqual(binding.external_order_id, "legacy-existing-1")
        self.assertEqual(binding.odoo_status, "rejected")
        self.assertEqual(self.env["bs.delivery.order"].search_count([
            ("pos_order_id", "=", order.id),
        ]), 1)
        self.assertEqual(canonical.state, "rejected")

    def test_status_callback_cannot_update_another_branch_order(self):
        order = self._create_order()
        order.with_context(bs_blend_bridge_sync=True).write({"blend_order_id": "blend-scope-1"})
        self.env["bs.delivery.blend.bridge"].ensure_order_binding(order, self.connection)
        other_config = self.env["pos.config"].create({
            "name": "Other Blend branch", "blend_branch_id": "another-%s" % self.branch_id,
            "blend_brand_id": self.brand_id,
        })
        self.connection.pos_config_ids |= other_config
        with self.assertRaises(BlendAPIError) as raised:
            self.webhook({
                "event": "order_updated", "branch_id": other_config.blend_branch_id,
                "payload": {"order_id": "blend-scope-1", "status": "prepared"},
            })
        self.assertEqual(raised.exception.status_code, 404)
        order.invalidate_recordset()
        self.assertNotEqual(order.blend_status, "prepared")

    def test_no_provider_specific_operational_table(self):
        concrete = [
            model_name
            for model_name, model in self.env.registry.models.items()
            if model_name.startswith("blend.") and getattr(model, "_auto", False)
        ]
        self.assertEqual(concrete, [])

    def test_update_store_availability_dispatches_store_updated_webhook(self):
        response = FakeResponse(200, {"success": True})
        with patch(
            "odoo.addons.bs_pos_delivery_blend.models.delivery_adapter.requests.post",
            return_value=response,
        ) as request_post:
            result = self.store.action_sync_availability()

        self.assertTrue(result)
        request_post.assert_called_once()
        called_url = request_post.call_args[0][0]
        called_kwargs = request_post.call_args[1]
        self.assertEqual(called_url, self.connection.blend_outbound_webhook_url)
        self.assertEqual(called_kwargs["json"]["event"], "store.updated")
        self.assertEqual(called_kwargs["json"]["branch_id"], self.store.remote_store_id)
        self.assertIn("status", called_kwargs["json"])

    def test_pos_work_time_shift_computation(self):
        self.config.write({
            "delivery_work_time_enabled": True,
            "delivery_work_start_time": 9.0,
            "delivery_work_end_time": 23.0,
        })
        self.store.invalidate_recordset()
        self.assertTrue(self.store.work_time_enabled)
        self.assertEqual(self.store.work_start_time, 9.0)
        self.assertEqual(self.store.work_end_time, 23.0)

    def _local_time(self, hour, minute=0):
        tz = self.config._get_delivery_timezone()
        return tz.localize(datetime(2026, 1, 5, hour, minute))

    def test_pos_two_work_shifts(self):
        self.store.write({
            "work_time_enabled": True,
            "work_start_time": 9.0,
            "work_end_time": 15.0,
            "second_shift_enabled": True,
            "work_start_time_2": 18.0,
            "work_end_time_2": 2.0,  # overnight
        })
        self.assertEqual(self.config.delivery_work_start_time_2, 18.0)
        expectations = {
            (8, 59): False,
            (9, 0): True,
            (14, 59): True,
            (15, 0): False,   # gap between shifts
            (17, 30): False,
            (18, 0): True,
            (23, 59): True,
            (1, 30): True,    # after midnight, still shift 2
            (2, 0): False,
        }
        for (hour, minute), expected in expectations.items():
            self.assertEqual(
                self.store.is_open_for_delivery(at=self._local_time(hour, minute)),
                expected,
                "%02d:%02d" % (hour, minute),
            )

        self.store.second_shift_enabled = False
        self.assertFalse(self.store.is_open_for_delivery(at=self._local_time(19, 0)))

    def test_second_shift_requires_distinct_times(self):
        with self.assertRaises(ValidationError):
            self.config.write({
                "delivery_second_shift_enabled": True,
                "delivery_work_start_time_2": 10.0,
                "delivery_work_end_time_2": 10.0,
            })

    def test_store_updated_payload_follows_work_shift(self):
        self.store.write({
            "work_time_enabled": True,
            "work_start_time": 9.0,
            "work_end_time": 15.0,
            "second_shift_enabled": True,
            "work_start_time_2": 18.0,
            "work_end_time_2": 23.0,
        })
        response = FakeResponse(200, {"success": True})
        for hour, expected in ((10, True), (16, False), (19, True)):
            with patch.object(
                type(self.store), "is_in_delivery_work_time",
                lambda rec, at=None, _h=hour: rec.pos_config_id.is_in_delivery_work_time(
                    at=self._local_time(_h)
                ),
            ), patch(
                "odoo.addons.bs_pos_delivery_blend.models.delivery_adapter.requests.post",
                return_value=response,
            ) as request_post:
                self.store.action_sync_availability()
            payload = request_post.call_args[1]["json"]
            self.assertEqual(payload["event"], "store.updated")
            self.assertEqual(payload["branch_id"], self.store.remote_store_id)
            self.assertIs(payload["status"], expected, "hour %s" % hour)
            self.assertEqual(self.store.last_availability_synced, expected)
