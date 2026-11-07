import base64
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from ..services import outbound
from ..services.pos_api import BlendPOSAPIService
from .common import BlendTransactionCase
from .test_outbound import FakeResponse


@tagged("post_install", "-at_install")
class TestBlendMenuNotifications(BlendTransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("ar_001")
        # Isolate deletion checks from any open demo sessions in the test DB.
        cls.env["pos.session"].search([("state", "!=", "closed")]).write({"state": "closed"})

    def setUp(self):
        super().setUp()
        self.category, self.other_category = self.env["pos.category"].create([
            {"name": "Notification category A"},
            {"name": "Notification category B"},
        ])
        self.config.write({
            "blend_branch_id": "90001",
            "blend_brand_id": "90000",
            "limit_categories": True,
            "iface_available_categ_ids": [Command.set(self.category.ids)],
        })
        self.other_config = self.env["pos.config"].create({
            "name": "Notification branch B",
            "blend_enabled": True,
            "blend_branch_id": "90002",
            "blend_brand_id": "90000",
            "blend_event_url": "https://blend.example.test/events",
            "blend_inbound_token": "notification-other-%s" % self.config.id,
            "limit_categories": True,
            "iface_available_categ_ids": [Command.set(self.other_category.ids)],
            "payment_method_ids": [Command.clear()],
        })
        self.products = self.env["product.template"]
        self.product = self._product(blend_inbound_sync=True)
        self.logs = self.env["blend.integration.log"]
        self._mark()

    def _product(self, *, blend_inbound_sync=False, **values):
        return self.products.with_context(blend_inbound_sync=blend_inbound_sync).create({
            "name": "Native menu product",
            "available_in_pos": True,
            "sale_ok": True,
            "company_id": self.env.company.id,
            "pos_categ_ids": [Command.set(self.category.ids)],
            **values,
        }).with_env(self.env)

    def _mark(self):
        self.last_log_id = self.logs.search([], order="id desc", limit=1).id or 0

    def _new_logs(self):
        return self.logs.search([
            ("id", ">", self.last_log_id),
            ("event", "=", "menu.updated"),
            ("direction", "=", "outbound"),
        ])

    def _assert_events(self, expected):
        logs = self._new_logs()
        actual = {
            (log.config_id.id, log.request_payload["entity"]["type"],
             log.request_payload["entity"]["id"])
            for log in logs
        }
        self.assertEqual(actual, expected)
        self.assertEqual(len(logs), len(expected), "Duplicate notifications in one operation")
        self.assertEqual(set(logs.mapped("state")), {"pending"} if expected else set())
        return logs

    def _target(self, record=None, config=None, kind="product"):
        record = record if record is not None else self.product
        config = config if config is not None else self.config
        return (config.id, kind, str(record.blend_external_id or record.id))

    def _option_group(self):
        option = self._product(blend_inbound_sync=True, name="Native option", use_as_modifier=True)
        group = self.env["modifiers.groups"].with_context(blend_inbound_sync=True).create({
            "name": "Native toppings",
            "modifier_ids": [Command.set(option.ids)],
        }).with_env(self.env)
        self.product.with_context(blend_inbound_sync=True).write({
            "modifier_grp_ids": [Command.set(group.ids)],
        })
        return option, group

    def test_native_product_create_matches_menu_scope(self):
        product = self._product()
        self.assertFalse(product.blend_config_id)
        self.assertFalse(product.blend_external_id)
        self._assert_events({self._target(product)})
        service = BlendPOSAPIService(self.env, self.config | self.other_config)
        self.assertIn(product, service._products_for_config(self.config))
        self.assertNotIn(product, service._products_for_config(self.other_config))
        menu = service.get_menu(filters={"product_id": str(product.id)})
        menu_ids = {item["id"] for category in menu["categories"] for item in category["products"]}
        self.assertEqual(menu_ids, {product.id})

    def test_non_menu_products_and_unused_dependencies_do_not_notify(self):
        self.products.create([
            {"name": "Not sold", "available_in_pos": True, "sale_ok": False},
            {"name": "Not in POS", "available_in_pos": False},
            {"name": "Unused option", "use_as_modifier": True, "available_in_pos": True},
        ])
        category = self.env["pos.category"].create({"name": "Unused category"})
        category.write({"name": "Unused category renamed"})
        group = self.env["modifiers.groups"].create({"name": "Unused group"})
        group.write({"name": "Unused group renamed"})
        self._assert_events(set())

    def test_menu_fields_and_archival_notify(self):
        for values in (
            {"name": "Renamed"}, {"public_description": "Menu description"},
            {"list_price": 42}, {"pos_sequence": 12}, {"active": False},
            {"active": True},
            {"blend_unavailable_config_ids": [Command.link(self.config.id)]},
        ):
            with self.subTest(values=values):
                self._mark()
                self.product.write(values)
                self._assert_events({self._target()})

    def test_unrelated_field_does_not_notify(self):
        self.product.write({"default_code": "INTERNAL-ONLY"})
        self._assert_events(set())

    def test_image_translation_and_variant_edits_notify(self):
        buffer = BytesIO()
        Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
        self.product.write({"image_1920": base64.b64encode(buffer.getvalue())})
        self._assert_events({self._target()})
        self._mark()
        self.product.update_field_translations("name", {"en_US": "Translated menu name"})
        self._assert_events({self._target()})
        self._mark()
        self.product.product_variant_id.write({"list_price": 35})
        self._assert_events({self._target()})

    def test_shared_product_notifies_each_matching_branch(self):
        self.product.with_context(blend_inbound_sync=True).write({
            "company_id": False,
            "pos_categ_ids": [Command.set((self.category | self.other_category).ids)],
        })
        self.product.write({"list_price": 18})
        self._assert_events({self._target(), self._target(config=self.other_config)})

    def test_membership_changes_notify_old_and_new_branches(self):
        self.product.write({"pos_categ_ids": [Command.set(self.other_category.ids)]})
        self._assert_events({self._target(), self._target(config=self.other_config)})
        for values in ({"sale_ok": False}, {"sale_ok": True}, {"available_in_pos": False}):
            with self.subTest(values=values):
                self._mark()
                self.product.write(values)
                self._assert_events({self._target(config=self.other_config)})

    def test_branch_mapping_and_id_changes_preserve_previous_targets(self):
        self.product.with_context(blend_inbound_sync=True).write({
            "pos_categ_ids": [Command.set((self.category | self.other_category).ids)],
            "blend_config_id": self.config.id, "blend_external_id": "91000",
        })
        self.product.write({"blend_config_id": self.other_config.id, "blend_external_id": "92000"})
        self._assert_events({
            (self.config.id, "product", "91000"),
            (self.other_config.id, "product", "92000"),
        })

    def test_deletion_keeps_the_removed_product_id(self):
        target = self._target()
        self.product.unlink()
        self._assert_events({target})

    def test_failed_deletion_does_not_notify(self):
        self.env["pos.session"].create({"config_id": self.config.id})
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.product.unlink()
        self._assert_events(set())
        self.assertTrue(self.product.exists())

    def test_modifier_edit_and_delete_notify_parent_products(self):
        option, _group = self._option_group()
        for values in ({"list_price": 8}, {"active": False}, {"active": True}):
            with self.subTest(values=values):
                self._mark()
                option.write(values)
                self._assert_events({self._target()})
        self._mark()
        option.unlink()
        self._assert_events({self._target()})

    def test_scoped_modifier_group_does_not_notify_another_branch(self):
        option, group = self._option_group()
        self.product.with_context(blend_inbound_sync=True).write({
            "pos_categ_ids": [Command.set((self.category | self.other_category).ids)],
        })
        group.with_context(blend_inbound_sync=True).write({"blend_config_id": self.config.id})
        option.write({"name": "Changed option"})
        self._assert_events({self._target()})

    def test_category_and_group_edit_and_delete(self):
        _option, group = self._option_group()
        for record, kind in ((self.category, "category"), (group, "modifier_group")):
            with self.subTest(kind=kind):
                self._mark()
                record.write({"name": "Changed menu entity"})
                self._assert_events({self._target(record, kind=kind)})
                self._mark()
                target = self._target(record, kind=kind)
                record.unlink()
                self._assert_events({target})
                # Restore product category membership for the next subtest.
                if kind == "category":
                    self.config.write({"limit_categories": False})

    def test_multi_record_changes_and_multiple_saves_in_one_second(self):
        second = self._product(blend_inbound_sync=True)
        frozen = fields.Datetime.now()
        with patch.object(fields.Datetime, "now", return_value=frozen):
            (self.product | second).write({"list_price": 11})
            first = self._assert_events({self._target(), self._target(second)})
            self._mark()
            self.product.write({"list_price": 12})
            following = self._assert_events({self._target()})
        self.assertNotIn(following.idempotency_key, first.mapped("idempotency_key"))

    def test_inbound_sync_and_rollback_do_not_send_notifications(self):
        product = self._product(blend_inbound_sync=True)
        product.with_context(blend_inbound_sync=True).write({"list_price": 8})
        product.with_context(blend_inbound_sync=True).unlink()
        self._assert_events(set())
        with self.assertRaisesRegex(ValueError, "Rollback"):
            with self.env.cr.savepoint():
                self.product.write({"list_price": 9})
                self.assertTrue(self._new_logs())
                message = "Rollback"
                raise ValueError(message)
        self._assert_events(set())

    def test_disabled_branch_or_missing_endpoint_does_not_notify(self):
        for values in ({"blend_enabled": False}, {"blend_enabled": True, "blend_event_url": False}):
            with self.subTest(values=values):
                self.config.write(values)
                self.product.write({"list_price": 5})
                self._assert_events(set())

    def test_company_isolation_and_product_editor_access(self):
        company = self.env["res.company"].create({"name": "Notification isolated company"})
        foreign = self._product(company_id=company.id)
        self._assert_events(set())
        self.assertNotIn(foreign, self.products._blend_menu_products(self.config))
        editor = new_test_user(
            self.env, login="blend_menu_editor",
            groups="base.group_user,product.group_product_manager,point_of_sale.group_pos_user",
        )
        self.product.with_user(editor).write({"name": "Edited by POS user"})
        self._assert_events({self._target()})
        self._mark()
        portal = new_test_user(self.env, login="blend_menu_portal", groups="base.group_portal")
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.product.with_user(portal).write({"name": "Unauthorized"})
        self._assert_events(set())

    def test_queued_native_product_event_uses_existing_sender(self):
        self.product.write({"list_price": 25})
        log = self._assert_events({self._target()})
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as send:
            self.assertTrue(log._process_outbound())
        self.assertEqual(send.call_args.args[0], self.config.blend_event_url)
        self.assertEqual(send.call_args.kwargs["json"]["entity"], {"type": "product", "id": str(self.product.id)})
        self.assertEqual(log.state, "success")
