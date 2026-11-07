import json
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests.common import new_test_user, tagged

from ..services import outbound
from ..services.menu_notifications import BlendMenuNotificationService
from .common import BlendTransactionCase
from .test_outbound import FakeResponse


@tagged("post_install", "-at_install")
class TestBlendSharedMenuNotifications(BlendTransactionCase):
    def setUp(self):
        super().setUp()
        self.url = "https://blend.example.test/shared-menu"
        self.category = self.env["pos.category"].create({"name": "Shared menu test category"})
        self.config.write({
            "blend_outbound_webhook_url": self.url,
            "limit_categories": True, "iface_available_categ_ids": [Command.set(self.category.ids)],
        })
        self.other = self.env["pos.config"].create({
            "name": "Shared menu branch B", "blend_enabled": True,
            "blend_brand_id": self.brand_id, "blend_branch_id": self.branch_id + "-B",
            "blend_outbound_webhook_url": self.url, "blend_outbound_token": self.outbound_token,
            "blend_inbound_token": self.token,
            "limit_categories": True, "iface_available_categ_ids": [Command.set(self.category.ids)],
            "payment_method_ids": [Command.clear()],
        })
        self.configs = self.config | self.other
        self.product = self._product()
        self.logs = self.env["blend.integration.log"]
        self._mark()

    def _product(self, **values):
        return self.env["product.template"].with_context(blend_inbound_sync=True).create({
            "name": "Shared menu test product", "sale_ok": True, "available_in_pos": True,
            "company_id": self.env.company.id, "pos_categ_ids": [Command.set(self.category.ids)],
            **values,
        }).with_env(self.env)

    def _mark(self):
        self.last_id = self.logs.search([], order="id desc", limit=1).id or 0

    def _new(self):
        return self.logs.search([
            ("id", ">", self.last_id), ("direction", "=", "outbound"), ("event", "=", "menu.updated"),
        ], order="id")

    def _change(self):
        self._mark()
        self.product.write({"list_price": self.product.list_price + 1})
        return self._new()

    def _assert_shared(self, logs, config_ids=None):
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs.menu_scope["config_ids"], sorted(config_ids or self.configs.ids))
        self.assertEqual(set(logs.request_payload), {"brand_id", "event_time", "event", "entity"})
        self.assertFalse(logs.stock_check)

    def _send(self, logs):
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as post:
            for log in logs:
                log._process_outbound()
        return post

    def test_name_and_price_in_one_save_send_one_brand_event(self):
        with patch.object(outbound.requests, "post") as post:
            self.product.write({"name": "Renamed shared product", "list_price": 31})
            post.assert_not_called()
        logs = self._new()
        self._assert_shared(logs)
        self.assertEqual(logs.config_id, self.configs.sorted("id")[:1])
        post = self._send(logs)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], self.url)
        self.assertEqual(post.call_args.kwargs["json"]["entity"], {"type": "product", "id": str(self.product.id)})
        self.assertEqual(logs.state, "success")

    def test_multiple_catalogue_fields_and_archive_each_generate_one_event(self):
        for values in ({"name": "New name"}, {"public_description": "New description"},
                       {"list_price": 32}, {"pos_sequence": 17}, {"active": False}, {"active": True}):
            with self.subTest(values=values):
                self._mark()
                self.product.write(values)
                self._assert_shared(self._new())

    def test_creation_variant_and_translation_edits_are_shared(self):
        self._product().with_context(blend_inbound_sync=False).write({"name": "Created then edited"})
        self._assert_shared(self._new())
        self._mark()
        self.product.product_variant_id.write({"list_price": 35})
        self._assert_shared(self._new())
        self._mark()
        self.product.update_field_translations("name", {"en_US": "New translated name"})
        self._assert_shared(self._new())

    def test_new_product_creation_is_shared(self):
        self.env["product.template"].create({
            "name": "New shared product", "sale_ok": True, "available_in_pos": True,
            "pos_categ_ids": [Command.set(self.category.ids)], "company_id": self.env.company.id,
        })
        self._assert_shared(self._new())

    def test_deletion_retains_the_old_entity_and_scope(self):
        # Native POS protects deletion while any demo/test session is open.
        # This fixture change is rolled back with the test transaction.
        self.env["pos.session"].search([("state", "!=", "closed")]).write({"state": "closed"})
        entity_id = str(self.product.id)
        self.product.unlink()
        log = self._new()
        self._assert_shared(log)
        self.assertEqual(log.request_payload["entity"]["id"], entity_id)
        self.assertEqual(self._send(log).call_count, 1)

    def test_separate_saves_in_same_second_are_not_merged(self):
        with patch.object(fields.Datetime, "now", return_value=fields.Datetime.now()):
            first = self._change()
            second = self._change()
        self._assert_shared(first)
        self._assert_shared(second)
        self.assertNotEqual(first.idempotency_key, second.idempotency_key)
        self.assertEqual(first.request_payload["event_time"], second.request_payload["event_time"])
        self.assertEqual(self._send(first | second).call_count, 2)

    def test_multi_product_write_is_one_event_per_product_not_per_branch(self):
        second = self._product()
        (self.product | second).write({"list_price": 12})
        logs = self._new()
        self.assertEqual(len(logs), 2)
        self.assertEqual({log.external_reference for log in logs}, {str(self.product.id), str(second.id)})
        for log in logs:
            self._assert_shared(log)

    def test_category_and_modifier_group_edits_are_shared(self):
        option = self._product(use_as_modifier=True)
        group = self.env["modifiers.groups"].with_context(blend_inbound_sync=True).create({
            "name": "Shared toppings", "modifier_ids": [Command.set(option.ids)],
        }).with_env(self.env)
        self.product.with_context(blend_inbound_sync=True).modifier_grp_ids = [Command.set(group.ids)]
        for record, kind in ((self.category, "category"), (group, "modifier_group"), (option, "product")):
            with self.subTest(kind=kind):
                self._mark()
                record.write({"name": "Updated %s" % kind})
                log = self._new()
                self._assert_shared(log)
                self.assertEqual(log.request_payload["entity"]["type"], kind)

    def test_distinct_brands_destinations_and_outbound_credentials_stay_separate(self):
        for field, value in (
            ("blend_brand_id", "another-brand"),
            ("blend_outbound_webhook_url", "https://blend.example.test/another-menu"),
            ("blend_outbound_token", "different-outbound-credential"),
        ):
            with self.subTest(field=field):
                old = self.other[field]
                self.other[field] = value
                logs = self._change()
                self.assertEqual(len(logs), 2)
                self.assertEqual({tuple(log.menu_scope["config_ids"]) for log in logs}, {(self.config.id,), (self.other.id,)})
                self.assertEqual(self._send(logs).call_count, 2)
                self.other[field] = old

    def test_effective_endpoint_can_be_shared_between_legacy_and_unified_config(self):
        self.other.write({"blend_outbound_webhook_url": False, "blend_event_url": self.url})
        self._assert_shared(self._change())

    def test_missing_brand_never_merges_unrelated_branches(self):
        self.configs.write({"blend_outbound_webhook_url": False, "blend_brand_id": False, "blend_event_url": self.url})
        logs = self._change()
        self.assertEqual(len(logs), 2)
        self.assertFalse(any(log.menu_scope for log in logs))

    def test_company_boundaries_are_never_merged(self):
        company = self.env["res.company"].create({"name": "Shared menu isolated company"})
        foreign = self.env["pos.config"].with_company(company).create({
            "name": "Other company menu", "company_id": company.id, "blend_enabled": True,
            "blend_brand_id": self.brand_id, "blend_branch_id": "other-company-branch",
            "blend_outbound_webhook_url": self.url, "blend_outbound_token": self.outbound_token,
            "blend_inbound_token": "other-company-test-inbound",
            "payment_method_ids": [Command.clear()], "limit_categories": True,
            "iface_available_categ_ids": [Command.set(self.category.ids)],
        })
        self.product.with_context(blend_inbound_sync=True).company_id = False
        logs = self._change()
        self.assertEqual(len(logs), 2)
        self._assert_shared(logs.filtered(lambda log: log.company_id == self.env.company))
        self._assert_shared(logs.filtered(lambda log: log.company_id == company), foreign.ids)

    def test_nested_hooks_merge_scope_without_losing_a_branch(self):
        notifier = BlendMenuNotificationService(self.env)
        # Reversed order deliberately discovers the lower-ID config later.
        notifier.enqueue({(self.other.id, "product", str(self.product.id))})
        notifier.enqueue({(self.config.id, "product", str(self.product.id))})
        notifier.enqueue({(self.other.id, "product", str(self.product.id))})
        log = self._new()
        self._assert_shared(log)
        self.assertEqual(self._send(log).call_count, 1)

    def test_already_attempted_scope_cannot_be_extended(self):
        notifier = BlendMenuNotificationService(self.env)
        notifier.enqueue({(self.config.id, "product", str(self.product.id))})
        self._send(self._new())
        with self.assertRaises(UserError):
            notifier.enqueue({(self.other.id, "product", str(self.product.id))})

    def test_stock_checks_are_not_merged_with_each_other_or_catalogue_changes(self):
        notifier = BlendMenuNotificationService(self.env)
        targets = {(config.id, "product", str(self.product.id)) for config in self.configs}
        notifier.enqueue(targets, stock_check={str(self.product.id): False})
        notifier.enqueue(targets)
        logs = self._new()
        self.assertEqual(len(logs), 3)
        stock_logs = logs.filtered("stock_check")
        self.assertEqual(set(stock_logs.mapped("config_id").ids), set(self.configs.ids))
        self.assertFalse(any(log.menu_scope for log in stock_logs))
        self._assert_shared(logs - stock_logs)

    def test_only_matching_branches_are_included(self):
        category_b = self.env["pos.category"].create({"name": "Other menu"})
        self.other.iface_available_categ_ids = [Command.set(category_b.ids)]
        self._assert_shared(self._change(), self.config.ids)
        self._mark()
        self.product.write({"pos_categ_ids": [Command.set(category_b.ids)]})
        # Notify the union of the previous and new menu membership.
        self._assert_shared(self._new())

    def test_disabled_branch_is_not_in_new_group(self):
        self.other.blend_enabled = False
        self._assert_shared(self._change(), self.config.ids)

    def test_inbound_sync_and_rollback_do_not_leak_notifications(self):
        self.product.with_context(blend_inbound_sync=True).list_price = 100
        self.assertFalse(self._new())
        with self.assertRaisesRegex(ValueError, "test rollback"), self.env.cr.savepoint():
            self._assert_shared(self._change())
            raise ValueError("test rollback")
        self.assertFalse(self._new())

    def test_product_editor_can_notify_all_branches_without_reading_credentials(self):
        editor = new_test_user(self.env, login="shared-menu-editor-%s" % self.config.id,
                               groups="base.group_user,product.group_product_manager,point_of_sale.group_pos_user")
        self.product.with_user(editor).write({"name": "Edited by product manager"})
        self._assert_shared(self._new())

    def test_retry_reuses_one_job_and_does_not_expose_scope_or_credentials(self):
        log = self._change()
        with patch.object(outbound.requests, "post", side_effect=[
            FakeResponse(503, {"message": "Unavailable"}), FakeResponse(200, {"status": "success"}),
        ]) as post:
            self.assertFalse(log._process_outbound())
            self.assertTrue(log._process_outbound())
            self.assertEqual(post.call_count, 2)
            self.assertEqual(post.call_args_list[0].kwargs["json"], post.call_args_list[1].kwargs["json"])
        self._assert_shared(log)
        self.assertEqual(log.attempt_count, 2)
        for value in (log.idempotency_key, json.dumps(log.menu_scope), json.dumps(log.request_payload), json.dumps(log.request_headers)):
            self.assertNotIn(self.outbound_token, value)

    def test_change_to_non_sender_branch_blocks_delivery(self):
        for values in (
            {"blend_outbound_webhook_url": "https://blend.example.test/changed"},
            {"blend_outbound_token": "rotated-only-one-branch"},
            {"blend_brand_id": "changed-brand"}, {"blend_enabled": False}, {"active": False},
        ):
            with self.subTest(values=values):
                log = self._change()
                old = {field: self.other[field] for field in values}
                self.other.write(values)
                with patch.object(outbound.requests, "post") as post:
                    self.assertFalse(log._process_outbound())
                    post.assert_not_called()
                self.assertEqual(log.state, "retry")
                with self.assertRaises(UserError):
                    log.action_retry()
                self.other.write(old)

    def test_explicit_retry_can_adopt_a_uniform_new_endpoint_or_token(self):
        for values in (
            {"blend_outbound_webhook_url": "https://blend.example.test/new-shared-menu"},
            {"blend_outbound_token": "uniform-rotated-credential"},
        ):
            with self.subTest(values=values):
                log = self._change()
                old_scope = log.menu_scope
                payload = log.request_payload
                self.configs.write(values)
                with patch.object(outbound.requests, "post") as post:
                    self.assertFalse(log._process_outbound())
                    post.assert_not_called()
                log.action_retry()
                self.assertNotEqual(log.menu_scope["destination_key"], old_scope["destination_key"])
                self.assertEqual(log.menu_scope["config_ids"], old_scope["config_ids"])
                self.assertEqual(log.request_payload, payload)
                self.assertEqual(self._send(log).call_count, 1)

    def test_reset_cannot_silently_change_brand_for_existing_payload(self):
        log = self._change()
        original = log.request_payload
        self.configs.blend_brand_id = "new-brand"
        with self.assertRaises(UserError):
            log.action_retry()
        self.assertEqual(log.request_payload, original)

    def test_identity_validation_includes_non_sender_branch(self):
        self._product(blend_external_id=str(self.product.id), blend_config_id=self.other.id)
        log = self._change()
        self._assert_shared(log)
        with patch.object(outbound.requests, "post") as post:
            self.assertFalse(log._process_outbound())
            post.assert_not_called()
        self.assertEqual(log.state, "retry")
        self.assertIn("Ambiguous", log.error_message)
