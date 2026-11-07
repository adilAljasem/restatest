import base64
from io import BytesIO

from PIL import Image
from odoo import Command, fields
from odoo.tools.mimetypes import guess_mimetype
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

import requests
from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo.tests import HttpCase, tagged
from odoo.addons.bs_pos_delivery_core.exceptions import DeliveryBusinessError
from odoo.exceptions import UserError


@tagged("post_install", "-at_install")
class TestTryOrderOrderHttp(TestPoSCommon, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref(
            "bs_pos_delivery_core.group_delivery_integration_manager"
        )
        provider = cls.env["bs.delivery.provider"].search([("code", "=", "tryorder")])
        cls.connection = cls.env["bs.delivery.connection"].create({
            "name": "TryOrder HTTP test",
            "provider_id": provider.id,
            "company_id": cls.basic_config.company_id.id,
            "state": "connected",
            "auth_scheme": "api_key_secret",
            "webhook_secret": "tryorder-secret-test",
            "pos_config_ids": [Command.set(cls.basic_config.ids)],
        })
        cls.store = cls.env["bs.delivery.store.binding"].create({
            "connection_id": cls.connection.id,
            "pos_config_id": cls.basic_config.id,
            "remote_store_id": str(cls.basic_config.id),
        })
        cls.connection.write({"delivery_payment_mapping_ids": [
            Command.create({
                "pos_config_id": cls.basic_config.id,
                "aggregator_name": "tryorder",
                "payment_type": "cash",
                "payment_method_id": cls.cash_pm1.id,
            }),
            Command.create({
                "pos_config_id": cls.basic_config.id,
                "aggregator_name": "tryorder",
                "payment_type": "credit_visa",
                "payment_method_id": cls.bank_pm1.id,
            }),
        ]})
        cls.product = cls.env["product.product"].create({
            "name": "TryOrder Meal",
            "available_in_pos": True,
            "list_price": 10,
            "taxes_id": [Command.clear()],
        })
        cls.env.flush_all()

    def test_provider_is_approved_for_production(self):
        self.assertEqual(self.connection.provider_id.implementation_state, "ready")

    def test_outbound_gateway_requires_https_url(self):
        self.connection.write({
            "base_url": "http://tryorder.example",
            "access_token": "outbound-secret-test",
        })

        with self.assertRaises(DeliveryBusinessError):
            self.env["bs.delivery.adapter.tryorder"].test_connection(self.connection)

    def test_production_inbound_rejects_provider_downgraded_from_ready(self):
        self.connection.environment = "production"
        self.connection.provider_id.sudo().implementation_state = "sandbox"
        self.env.flush_all()

        response = self._post(order_id="TRY-DOWNGRADED-PROVIDER")
        self.assertEqual(response.status_code, 401)

    def _post(self, key="tryorder-secret-test", *, order_id="TRY-HTTP-1", modifiers=None,
              overrides=None):
        order = {
            "branch_id": self.basic_config.id,
            "provider_order_id": order_id,
            "items": [{
                "product_id": self.product.product_tmpl_id.id,
                "qty": 2,
                "price": 10,
                **({"modifiers": modifiers} if modifiers is not None else {}),
            }],
            "payment_type": "cash",
        }
        order.update(overrides or {})
        return self.url_open(
            "/order_connector/orders",
            method="POST",
            json={"order": order},
            params={"db": self.env.cr.dbname},
            headers={
                "X-Api-Key": key,
                "X-Odoo-Database": self.env.cr.dbname,
            },
        )

    def _delivery_for_response(self, response):
        """Resolve the internal Core order from either external response shape."""
        order_id = int(response.json()["order_id"])
        delivery = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("pos_order_id", "=", order_id),
        ], limit=1)
        return delivery or self.env["bs.delivery.order"].browse(order_id)

    def test_failed_http_log_retries_order_without_event(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Retry Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        payload = {"order": {
            "branch_id": self.basic_config.id,
            "provider_order_id": "TRY-LOG-RETRY",
            "items": [{"product_id": self.product.product_tmpl_id.id,
                       "qty": 2, "price": 10}],
            "payment_type": "cash",
        }}
        log = self.env["bs.delivery.sync.log"].start_attempt(
            self.connection, direction="incoming", operation="tryorder:create_order",
            external_id="TRY-LOG-RETRY", request_method="POST",
            request_url="/order_connector/orders", request_payload=payload,
        )
        log.mark_failed("Previous configuration error", http_status=422)
        self.assertTrue(log.can_retry_processing)
        log.action_retry_processing()
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "TRY-LOG-RETRY"),
        ])
        self.assertEqual(order.pos_processing_state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        self.assertFalse(order.pos_order_id.account_move.amount_residual)
        pos_id = order.pos_order_id.id
        with self.assertRaises(UserError):
            log.action_retry_processing()
        self.assertEqual(order.pos_order_id.id, pos_id)

    def _failed_create_log(self, reference, order_values, error):
        payload = {"order": {
            "branch_id": self.basic_config.id,
            "provider_order_id": reference,
            "items": [{"product_id": self.product.product_tmpl_id.id,
                       "qty": 2, "price": 10}],
            "payment_type": "cash",
            **order_values,
        }}
        log = self.env["bs.delivery.sync.log"].start_attempt(
            self.connection, direction="incoming", operation="tryorder:create_order",
            external_id=reference, request_method="POST",
            request_url="/order_connector/orders", request_payload=payload,
        )
        log.mark_failed(error, http_status=422)
        return log

    def test_force_send_total_mismatch_order_to_branch(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Force Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
            "auto_print_kitchen": True,
        })
        log = self._failed_create_log(
            "TRY-FORCE-1", {"total": 25},
            "TryOrder total differs from product and tax amounts",
        )
        log.action_retry_processing()
        self.assertEqual(log.status, "failed")
        self.assertTrue(log.can_force_send)
        action = log.action_force_send_to_branch()
        self.assertEqual(action["params"]["type"], "success", action)
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "TRY-FORCE-1"),
        ])
        self.assertTrue(order.pos_order_id)
        self.assertEqual(order.amount_total, order.pos_order_id.amount_total)
        self.assertEqual(log.status, "success")
        self.assertTrue(any("Force-sent" in str(body or "")
                            for body in order.message_ids.mapped("body")))
        log.sudo().write({"status": "failed"})
        with self.assertRaises(UserError):
            log.action_force_send_to_branch()

    def test_force_send_accepts_product_closed_in_branch(self):
        self.basic_config.open_ui()
        log = self._failed_create_log(
            "TRY-FORCE-CLOSED", {}, "Product is unavailable in this branch",
        )
        self.product.available_in_pos = False
        log.action_retry_processing()
        self.assertEqual(log.status, "failed")
        action = log.action_force_send_to_branch()
        self.assertEqual(action["params"]["type"], "success", action)

    def test_every_tryorder_order_uses_the_delivery_preset(self):
        Preset = self.env["pos.preset"]
        dine_in = Preset.create({"name": "Dine In"})
        delivery = Preset.create({"name": "Delivery", "identification": "address"})
        self.basic_config.write({
            "use_presets": True,
            "default_preset_id": dine_in.id,
            "available_preset_ids": [Command.set([dine_in.id, delivery.id])],
        })
        self.basic_config.open_ui()
        for order_type in ("pickup", "dine_in", "delivery"):
            order = self.env["bs.delivery.tryorder.intake"].receive_order({
                "branch_id": self.basic_config.id,
                "provider_order_id": "TRY-PRESET-%s" % order_type,
                "order_type": order_type,
                "items": [{"product_id": self.product.product_tmpl_id.id,
                           "qty": 1, "price": 10}],
                "payment_type": "cash",
            }, "tryorder-secret-test")
            self.assertEqual(order._pos_order_preset(order.store_binding_id), delivery,
                             order_type)

    def test_failed_status_log_accepts_and_invoices_pending_order(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Status Retry Customer"})
        self.connection.delivery_default_invoice_partner_id = partner
        order = self.env["bs.delivery.tryorder.intake"].receive_order({
            "branch_id": self.basic_config.id,
            "provider_order_id": "TRY-STATUS-RETRY",
            "items": [{"product_id": self.product.product_tmpl_id.id,
                       "qty": 2, "price": 10}],
            "payment_type": "cash",
        }, "tryorder-secret-test")
        self.assertEqual(order.state, "received")
        log = self.env["bs.delivery.sync.log"].start_attempt(
            self.connection, direction="incoming", operation="tryorder:update_status",
            request_method="PUT",
            request_url="/order_connector/orders/%s/status" % order.id,
            request_payload={"status": "accepted"},
        )
        log.mark_failed("Previous status error", http_status=422)
        log.action_retry_processing()
        self.assertEqual(order.state, "accepted")
        self.assertEqual(order.pos_processing_state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")

    def test_retry_pos_log_resumes_failed_automatic_acceptance(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Blocked Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        journal = self.basic_config.invoice_journal_id
        self.basic_config.invoice_journal_id = False
        order = self.env["bs.delivery.tryorder.intake"].receive_order({
            "branch_id": self.basic_config.id,
            "provider_order_id": "TRY-BLOCKED-RETRY",
            "items": [{"product_id": self.product.product_tmpl_id.id,
                       "qty": 2, "price": 10}],
            "payment_type": "cash",
        }, "tryorder-secret-test")
        self.assertEqual(order.state, "received")
        self.assertEqual(order.pos_processing_state, "blocked")
        log = self.env["bs.delivery.sync.log"].search([
            ("binding_id", "=", order.binding_id.id),
            ("operation", "=", "core:pos_processing"),
            ("status", "=", "failed"),
        ], limit=1)
        self.assertTrue(log)
        self.basic_config.invoice_journal_id = journal
        log.action_retry_processing()
        self.assertEqual(order.state, "accepted")
        self.assertEqual(order.pos_processing_state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")

    def test_catalog_prefers_menu_pricelist_then_product_sale_price(self):
        pricelist = self.env["product.pricelist"].create({
            "name": "TryOrder Menu Prices",
            "company_id": self.basic_config.company_id.id,
            "currency_id": self.basic_config.company_id.currency_id.id,
            "item_ids": [Command.create({
                "applied_on": "0_product_variant",
                "product_id": self.product.id,
                "compute_price": "fixed",
                "fixed_price": 7.5,
            })],
        })
        self.connection.menu_pricelist_id = pricelist

        def catalog_price():
            self.env.flush_all()
            response = self.url_open(
                "/order_connector/catalog",
                params={"db": self.env.cr.dbname, "branch_id": self.basic_config.id},
                headers={
                    "X-Api-Key": "tryorder-secret-test",
                    "X-Odoo-Database": self.env.cr.dbname,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            item = next(row for row in response.json()["items"]
                        if row["_id"] == str(self.product.product_tmpl_id.id))
            return item["price"]

        self.assertEqual(catalog_price(), 7.5)
        self.connection.menu_pricelist_id = False
        self.assertEqual(catalog_price(), 10)

    def test_rejected_product_is_visible_in_synchronization_log(self):
        external_id = "TRY-AUDIT-UNKNOWN-PRODUCT"
        response = self._post(order_id=external_id, overrides={
            "items": [{"product_id": 999999999, "qty": 1, "price": 10}],
        })

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["success"], False)
        logs = self.env["bs.delivery.sync.log"].search([
            ("connection_id", "=", self.connection.id),
            ("external_id", "=", external_id),
            ("operation", "=", "tryorder:create_order"),
        ])
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs.status, "failed")
        self.assertEqual(logs.http_status, 422)
        self.assertIn("Unknown or ambiguous product_id", logs.error_message)
        self.assertIn("999999999", logs.request_payload)
        self.assertIn("Unknown or ambiguous product_id", logs.response_payload)
        self.assertFalse(self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", external_id),
        ]))

    def test_rejected_modifier_identifies_product_position_and_unlinked_group(self):
        linked_group = self.env["pos.modifier.group"].create({
            "name": "Linked options", "selection_type": "multiple",
            "max_qty": 3, "company_id": self.basic_config.company_id.id,
        })
        valid = self.env["pos.modifier.option"].create({
            "name": "No onions", "group_id": linked_group.id,
            "option_type": "instruction", "operation": "remove",
        })
        other_group = linked_group.copy({"name": "Not linked"})
        rejected = valid.copy({"name": "Unlinked option", "group_id": other_group.id})
        self.product.product_tmpl_id.write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({
                "modifier_group_id": linked_group.id,
            })],
        })
        external_id = "TRY-MOD-DIAGNOSTIC-UNLINKED"
        self.env.flush_all()

        response = self._post(order_id=external_id, overrides={
            "items": [
                {"product_id": self.product.product_tmpl_id.id, "qty": 1, "price": 10},
                {
                    "product_id": self.product.product_tmpl_id.id,
                    "qty": 1, "price": 10,
                    "modifiers": [
                        {"id": f"posmod-{valid.id}", "qty": 1, "price": 0},
                        {"id": f"posmod-{rejected.id}", "qty": 1, "price": 0},
                    ],
                },
            ],
        })

        self.assertEqual(response.status_code, 422, response.text)
        payload = response.json()
        self.assertEqual(set(payload), {"success", "error"})
        self.assertIs(payload["success"], False)
        message = payload["error"]
        self.assertIn(f"product {self.product.product_tmpl_id.id}", message)
        self.assertIn(f"modifier posmod-{rejected.id}", message)
        self.assertIn("order.items[1].modifiers[1]", message)
        self.assertIn("group is not linked to this product", message)
        log = self.env["bs.delivery.sync.log"].search([
            ("connection_id", "=", self.connection.id),
            ("external_id", "=", external_id),
            ("operation", "=", "tryorder:create_order"),
        ])
        self.assertEqual(len(log), 1)
        self.assertEqual(log.http_status, 422)
        self.assertEqual(log.error_message, message)
        self.assertIn(message, log.response_payload)

    def test_rejected_modifier_identifies_inactive_option(self):
        group = self.env["pos.modifier.group"].create({
            "name": "Inactive option group", "selection_type": "multiple",
            "max_qty": 2, "company_id": self.basic_config.company_id.id,
        })
        option = self.env["pos.modifier.option"].create({
            "name": "Inactive option", "group_id": group.id,
            "option_type": "instruction", "operation": "remove",
            "active": False,
        })
        self.product.product_tmpl_id.write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": group.id})],
        })
        self.env.flush_all()

        response = self._post(order_id="TRY-MOD-DIAGNOSTIC-INACTIVE", modifiers=[{
            "id": f"posmod-{option.id}", "qty": 1, "price": 0,
        }])

        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn(f"modifier posmod-{option.id}", response.json()["error"])
        self.assertIn("order.items[0].modifiers[0]", response.json()["error"])
        self.assertIn("option is inactive", response.json()["error"])

    def test_legacy_create_response_identifies_pos_order(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Legacy Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        response = self._post(order_id="TRY-LEGACY-POS-1")
        self.assertEqual(response.status_code, 200, response.text)
        core = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "TRY-LEGACY-POS-1"),
        ])
        self.assertTrue(core.pos_order_id, core.pos_processing_error)
        self.assertEqual(response.json(), {
            "success": True,
            "order_id": str(core.pos_order_id.id),
            "order_name": core.pos_order_id.name or core.pos_order_id.pos_reference,
            "state": core.pos_order_id.state,
            "kind": "pos",
        })
        replay = self._post(order_id="TRY-LEGACY-POS-1")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json(), response.json())

    def test_legacy_status_uses_returned_pos_id(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Legacy Status"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        created = self._post(order_id="TRY-LEGACY-STATUS-1")
        self.assertEqual(created.status_code, 200, created.text)
        response = self.url_open(
            f"/order_connector/orders/{created.json()['order_id']}/status",
            method="PUT", json={"status": "preparing"},
            params={"db": self.env.cr.dbname},
            headers={"X-Api-Key": "tryorder-secret-test",
                     "X-Odoo-Database": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 200, response.text)
        core = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "TRY-LEGACY-STATUS-1"),
        ])
        self.assertEqual(core.state, "preparing")
        self.assertEqual(response.json(), {
            "success": True,
            "order_id": created.json()["order_id"],
            "state": core.pos_order_id.state,
            "status": "preparing",
        })
        replay = self.url_open(
            f"/order_connector/orders/{created.json()['order_id']}/status",
            method="POST", json={"status": "preparing"},
            params={"db": self.env.cr.dbname},
            headers={"X-Api-Key": "tryorder-secret-test",
                     "X-Odoo-Database": self.env.cr.dbname},
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json(), response.json())

    def test_terminal_statuses_require_separate_orders(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Terminal Status"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()

        def update(order_id, status):
            return self.url_open(
                f"/order_connector/orders/{order_id}/status",
                method="PUT", json={"status": status},
                params={"db": self.env.cr.dbname},
                headers={"X-Api-Key": "tryorder-secret-test",
                         "X-Odoo-Database": self.env.cr.dbname},
            )

        delivered = self._post(order_id="TRY-STATUS-DELIVERED-1")
        self.assertEqual(delivered.status_code, 200, delivered.text)
        delivered_id = delivered.json()["order_id"]
        self.assertEqual(update(delivered_id, "completed").status_code, 200)
        self.assertEqual(update(delivered_id, "rejected").status_code, 409)
        self.assertEqual(update(delivered_id, "canceled").status_code, 409)

        rejected = self._post(order_id="TRY-STATUS-REJECTED-1")
        self.assertEqual(rejected.status_code, 200, rejected.text)
        rejected_id = rejected.json()["order_id"]
        rejected_update = update(rejected_id, "rejected")
        self.assertEqual(rejected_update.status_code, 200, rejected_update.text)
        self.assertTrue(rejected_update.json()["success"])
        self.assertEqual(rejected_update.json()["order_id"], rejected_id)
        canceled = self._post(order_id="TRY-STATUS-CANCELED-1")
        self.assertEqual(canceled.status_code, 200, canceled.text)
        canceled_id = canceled.json()["order_id"]
        canceled_update = update(canceled_id, "canceled")
        self.assertEqual(canceled_update.status_code, 200, canceled_update.text)
        self.assertTrue(canceled_update.json()["success"])
        self.assertEqual(canceled_update.json()["order_id"], canceled_id)

    def test_legacy_attribute_modifier_keeps_parent_tax_and_order_total(self):
        tax = self.env["account.tax"].create({
            "name": "TryOrder Modifier Tax 10%", "amount": 10,
            "company_id": self.basic_config.company_id.id,
        })
        self.product.taxes_id = [Command.set(tax.ids)]
        attribute = self.env["product.attribute"].create({
            "name": "Legacy Sauce", "create_variant": "no_variant",
            "value_ids": [Command.create({"name": "Extra Sauce"})],
        })
        self.product.product_tmpl_id.write({
            "attribute_line_ids": [Command.create({
                "attribute_id": attribute.id,
                "value_ids": [Command.set(attribute.value_ids.ids)],
            })],
        })
        ptav = self.product.product_tmpl_id.attribute_line_ids.product_template_value_ids
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Legacy Modifier"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        catalog = self.url_open(
            "/order_connector/catalog",
            params={"db": self.env.cr.dbname, "branch_id": self.basic_config.id},
            headers={"X-Api-Key": "tryorder-secret-test",
                     "X-Odoo-Database": self.env.cr.dbname},
        )
        self.assertEqual(catalog.status_code, 200, catalog.text)
        item = next(row for row in catalog.json()["items"]
                    if row["_id"] == str(self.product.product_tmpl_id.id))
        self.assertIn(f"ptav-{ptav.id}", [
            row["modifier"]["_id"] for row in item["add_on"]
        ])
        response = self._post(order_id="TRY-LEGACY-PTAV-1", modifiers=[{
            "id": f"ptav-{ptav.id}", "qty": 1, "price": 2,
        }], overrides={"total": 26.4})
        self.assertEqual(response.status_code, 200, response.text)
        core = self._delivery_for_response(response)
        self.assertEqual(core.amount_total, 26.4)
        self.assertEqual(core.vat_amount, 2.4)
        self.assertEqual(core.pos_order_id.amount_total, 26.4)
        self.assertEqual(core.pos_order_id.amount_tax, 2.4)
        option_product = core.line_ids.filtered("is_modifier").product_id
        self.assertFalse(option_product.available_in_pos)
        loaded = self.env["product.template"].load_product_from_pos(
            self.basic_config.id,
            [("id", "=", option_product.product_tmpl_id.id)],
        )
        self.assertIn(option_product.id, [row["id"] for row in loaded["product.product"]])
        self.assertEqual(self.product.list_price, 10)
        self.assertEqual(self.product.taxes_id, tax)
        catalog_after = self.url_open(
            "/order_connector/catalog",
            params={"db": self.env.cr.dbname, "branch_id": self.basic_config.id},
            headers={"X-Api-Key": "tryorder-secret-test",
                     "X-Odoo-Database": self.env.cr.dbname},
        )
        self.assertEqual(catalog_after.status_code, 200, catalog_after.text)
        self.assertFalse(any(
            row["sku"].startswith("TRYORDER-PTAV-")
            for row in catalog_after.json()["items"]
        ))

    def test_delivery_fee_is_preserved_in_core_and_pos(self):
        fee_product = self.env["product.product"].create({
            "name": "TryOrder Delivery Fee", "type": "service",
            "available_in_pos": True, "taxes_id": [Command.clear()],
        })
        self.store.delivery_fee_product_id = fee_product
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Fee Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        response = self._post(order_id="TRY-FEE-1", overrides={
            "delivery_fee": 3, "total": 23,
        })
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.delivery_fee, 3)
        self.assertEqual(order.amount_total, 23)
        self.assertTrue(order.pos_order_id, order.pos_processing_error)
        self.assertEqual(order.pos_order_id.amount_total, 23)

    def test_one_decimal_financial_match_completes_pos_accounting(self):
        tax = self.env["account.tax"].create({
            "name": "TryOrder rounding tax", "amount": 15,
            "amount_type": "percent", "type_tax_use": "sale",
            "company_id": self.basic_config.company_id.id,
        })
        self.product.taxes_id = [Command.set(tax.ids)]
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Rounding Customer"})
        self.connection.delivery_default_invoice_partner_id = partner
        self.env.flush_all()

        response = self._post(order_id="TRY-ROUNDING-1", overrides={
            "items": [{"product_id": self.product.product_tmpl_id.id,
                       "qty": 1, "price": 37.4}],
            "total": 43.01,
        })
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.vat_amount, 5.61)
        self.assertEqual(order.amount_total, 43.01)
        order._with_delivery_internal_update().write({
            "vat_amount": 5.6,
            "amount_total": 43.0,
            "line_ids": [Command.update(order.line_ids.id, {"vat_amount": 5.6})],
        })
        order.payment_allocation_ids.amount = 43.0

        order.transition_to("accepted", source="webhook")
        self.assertEqual(order.pos_processing_state, "done", order.pos_processing_error)
        self.assertEqual(order.pos_order_id.amount_total, 43.0)
        self.assertEqual(order.pos_order_id.amount_paid, 43.0)
        self.assertEqual(order.pos_order_id.amount_tax, 5.61)
        self.assertEqual(order.pos_order_id.account_move.amount_total, 43.0)
        self.assertTrue(order.pos_order_id.lines.filtered(
            lambda line: line.name == "TryOrder rounding adjustment" and line.price_unit == -0.01
        ))

    def test_one_decimal_financial_difference_still_blocks_pos(self):
        tax = self.env["account.tax"].create({
            "name": "TryOrder mismatched tax", "amount": 15,
            "amount_type": "percent", "type_tax_use": "sale",
            "company_id": self.basic_config.company_id.id,
        })
        self.product.taxes_id = [Command.set(tax.ids)]
        self.basic_config.open_ui()
        self.env.flush_all()
        response = self._post(order_id="TRY-ROUNDING-REJECT", overrides={
            "items": [{"product_id": self.product.product_tmpl_id.id,
                       "qty": 1, "price": 37.4}],
            "total": 43.01,
        })
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        order._with_delivery_internal_update().write({
            "vat_amount": 5.54,
            "amount_total": 42.94,
            "line_ids": [Command.update(order.line_ids.id, {"vat_amount": 5.54})],
        })
        order.payment_allocation_ids.amount = 42.94

        with self.assertRaises(UserError), self.env.cr.savepoint():
            order.transition_to("accepted", source="webhook")
        self.assertFalse(order.pos_order_id)

    def test_zero_rated_delivery_fee_keeps_provider_total_and_invoice(self):
        zero_tax = self.env["account.tax"].create({
            "name": "TryOrder 0% Fee", "amount": 0,
            "amount_type": "percent", "type_tax_use": "sale",
            "price_include_override": "tax_excluded",
            "company_id": self.basic_config.company_id.id,
        })
        fee_product = self.env["product.product"].create({
            "name": "TryOrder Zero Rated Fee", "type": "service",
            "available_in_pos": True,
            "taxes_id": [Command.set(zero_tax.ids)],
        })
        self.store.delivery_fee_product_id = fee_product
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Zero Fee Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        response = self._post(order_id="TRY-FEE-ZERO-RATED", overrides={
            "delivery_fee": 3, "total": 23,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["kind"], "pos", response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.delivery_fee, 3)
        self.assertEqual(order.amount_total, 23)
        self.assertEqual(order.vat_amount, 0)
        self.assertEqual(order.pos_order_id.amount_total, 23)
        self.assertEqual(order.pos_order_id.account_move.amount_total, 23)

    def test_taxed_delivery_fee_is_rejected(self):
        tax = self.env["account.tax"].create({
            "name": "TryOrder 15% Fee", "amount": 15,
            "amount_type": "percent", "type_tax_use": "sale",
            "price_include_override": "tax_excluded",
            "company_id": self.basic_config.company_id.id,
        })
        fee_product = self.env["product.product"].create({
            "name": "TryOrder Taxed Fee", "type": "service",
            "taxes_id": [Command.set(tax.ids)],
        })
        self.store.delivery_fee_product_id = fee_product
        self.env.flush_all()
        response = self._post(order_id="TRY-FEE-TAXED", overrides={
            "delivery_fee": 3, "total": 23,
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("delivery fee product must be untaxed", response.json()["error"])

    def test_wallet_is_collected_payment_not_price_discount(self):
        self.connection.write({"delivery_payment_mapping_ids": [Command.create({
            "pos_config_id": self.basic_config.id,
            "aggregator_name": "tryorder",
            "payment_type": "wallet",
            "payment_method_id": self.bank_pm1.id,
        })]})
        self.env.flush_all()
        response = self._post(order_id="TRY-WALLET-1", overrides={
            "wallet_amount": 5,
            "discounts": [{"label": "Wallet", "amount": 5}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.amount_total, 20)
        self.assertEqual(order.discount_amount, 0)
        wallet = order.payment_allocation_ids.filtered(
            lambda allocation: allocation.external_payment_code == "wallet"
        )
        self.assertEqual(wallet.amount, 5)
        self.assertEqual(wallet.collection_status, "collected")
        self.assertEqual(sum(order.payment_allocation_ids.mapped("amount")), 20)

    def test_legacy_aggregate_discount_waits_for_pos_without_changing_item_price(self):
        response = self._post(order_id="TRY-DISCOUNT-1", overrides={"discount": 2})
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.items_subtotal, 20)
        self.assertEqual(order.discount_amount, 2)
        self.assertEqual(order.amount_total, 18)
        self.assertFalse(order.pos_order_id)
        self.assertEqual(order.line_ids.price_unit, 10)

    def test_coupon_loyalty_wallet_preserve_tax_core_pos_and_invoice(self):
        tax = self.env["account.tax"].create({
            "name": "TryOrder Discount Tax 10%", "amount": 10,
            "company_id": self.basic_config.company_id.id,
        })
        self.product.taxes_id = [Command.set(tax.ids)]
        self.connection.write({"delivery_payment_mapping_ids": [Command.create({
            "pos_config_id": self.basic_config.id,
            "aggregator_name": "tryorder",
            "payment_type": "wallet",
            "payment_method_id": self.bank_pm1.id,
        })]})
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Discount Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        discount_order = {
            "wallet_amount": 4,
            "discounts": [
                {"label": "Coupon", "amount": 2},
                {"label": "Loyalty", "amount": 3},
                {"label": "Wallet", "amount": 4},
            ],
            "total": 17,
        }
        response = self._post(order_id="TRY-DISCOUNT-MIX-1", overrides=discount_order)
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.items_subtotal, 20)
        self.assertEqual(order.discount_amount, 5)
        self.assertEqual(order.vat_amount, 2)
        self.assertEqual(order.amount_total, 17)
        self.assertEqual(order.pos_order_id.amount_total, 17)
        self.assertEqual(order.pos_order_id.amount_tax, 2)
        self.assertEqual(order.pos_order_id.account_move.amount_total, 17)
        self.assertEqual(order.pos_order_id.account_move.amount_tax, 2)
        discount_lines = order.pos_order_id.lines.filtered(lambda line: line.price_unit < 0)
        self.assertEqual(sorted(discount_lines.mapped("price_unit")), [-3, -2])
        self.assertTrue(all(not line.tax_ids for line in discount_lines))
        discount_product = discount_lines.product_id
        self.assertEqual(len(discount_product), 1)
        self.assertFalse(discount_product.sale_ok)
        self.assertFalse(discount_product.available_in_pos)
        # POS fetches missing products referenced by an incoming order by ID.
        loaded = self.env["product.template"].load_product_from_pos(
            self.basic_config.id,
            [("id", "=", discount_product.product_tmpl_id.id)],
        )
        self.assertIn(discount_product.id, [row["id"] for row in loaded["product.product"]])
        self.assertEqual(sum(order.payment_allocation_ids.mapped("amount")), 17)
        replay = self._post(order_id="TRY-DISCOUNT-MIX-1", overrides=discount_order)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["order_id"], response.json()["order_id"])
        self.assertEqual(len(order.pos_order_id.lines.filtered(lambda line: line.price_unit < 0)), 2)
        self.assertEqual(self.product.list_price, 10)
        self.assertEqual(self.product.taxes_id, tax)

    def test_coupon_does_not_change_tax_included_product_or_invoice(self):
        tax = self.env["account.tax"].create({
            "name": "TryOrder Included Discount Tax 5%", "amount": 5,
            "amount_type": "percent", "type_tax_use": "sale",
            "price_include_override": "tax_included",
            "company_id": self.basic_config.company_id.id,
        })
        self.product.taxes_id = [Command.set(tax.ids)]
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Included Discount"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        response = self._post(order_id="TRY-DISCOUNT-INCLUDED-1", overrides={
            "discounts": [{"label": "Coupon", "amount": 2}],
            "total": 18,
        })
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.discount_amount, 2)
        self.assertEqual(order.amount_total, 18)
        self.assertEqual(order.pos_order_id.amount_total, 18)
        self.assertEqual(order.pos_order_id.account_move.amount_total, 18)
        self.assertEqual(order.vat_amount, order.pos_order_id.amount_tax)
        self.assertEqual(order.vat_amount, order.pos_order_id.account_move.amount_tax)
        self.assertEqual(self.product.taxes_id, tax)

    def _limited_modifier(self, *, option_type="product"):
        extra = self.env["product.product"].create({
            "name": "Limited Topping", "available_in_pos": True,
            "list_price": 2, "taxes_id": [Command.clear()],
        })
        group = self.env["pos.modifier.group"].create({
            "name": "Limited Extras", "max_qty": 1,
            "company_id": self.basic_config.company_id.id,
        })
        values = {"name": "Limited Topping", "group_id": group.id, "option_type": option_type}
        if option_type == "product":
            values.update({
                "operation": "add", "product_id": extra.id,
                "price_mode": "fixed_extra", "extra_price": 2,
            })
        option = self.env["pos.modifier.option"].create(values)
        self.product.product_tmpl_id.write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": group.id})],
        })
        self.env.flush_all()
        return option, extra

    def _delivery_by_reference(self, reference):
        return self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", reference),
        ])

    def test_modifier_total_for_all_units_is_read_per_item(self):
        """2 items with the modifier qty 2 means 1 per item, not 2 each."""
        option, extra = self._limited_modifier()
        response = self._post(order_id="TRY-MOD-QTY-1", modifiers=[{
            "id": f"posmod-{option.id}", "qty": 2, "price": 2,
        }])
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_by_reference("TRY-MOD-QTY-1")
        child = order.line_ids.filtered(lambda line: line.product_id == extra)
        self.assertEqual(child.quantity, 2)  # 1 per item x 2 items

    def test_instruction_total_for_all_units_is_accepted(self):
        """The real case: 2 Smash Burgers, "Without pickles" qty 2."""
        option, _extra = self._limited_modifier(option_type="instruction")
        response = self._post(order_id="TRY-INSTR-QTY", modifiers=[{
            "id": f"posmod-{option.id}", "qty": 2, "price": 0,
        }])
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_by_reference("TRY-INSTR-QTY")
        instruction = order.line_ids.filtered(
            lambda line: line.external_product_id == f"posinstr-{option.id}"
        )
        self.assertEqual(instruction.quantity, 2)

    def test_modifier_above_limit_is_prepared_as_ordered(self):
        """The customer paid on the platform: a limit breach does not drop the order."""
        option, extra = self._limited_modifier()
        response = self._post(order_id="TRY-MOD-OVER", modifiers=[{
            "id": f"posmod-{option.id}", "qty": 3, "price": 2,
        }])
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_by_reference("TRY-MOD-OVER")
        child = order.line_ids.filtered(lambda line: line.product_id == extra)
        self.assertEqual(child.quantity, 6)  # 3 per item x 2 items

    def test_product_image_requires_catalog_scope_token(self):
        response = self.url_open(
            f"/public/product/image/{self.product.product_tmpl_id.id}",
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 404)

    def test_catalog_image_url_serves_only_its_scoped_product(self):
        self.product.product_tmpl_id.image_512 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/Hf8AAAAASUVORK5CYII="
        )
        self.env.flush_all()
        catalog = self.url_open(
            "/order_connector/catalog",
            params={"db": self.env.cr.dbname, "branch_id": self.basic_config.id},
            headers={"X-Api-Key": "tryorder-secret-test",
                     "X-Odoo-Database": self.env.cr.dbname},
        )
        self.assertEqual(catalog.status_code, 200, catalog.text)
        item = next(row for row in catalog.json()["items"]
                    if row["_id"] == str(self.product.product_tmpl_id.id))
        url = urlsplit(item["image_url"])
        params = {key: values[0] for key, values in parse_qs(url.query).items()}
        params["db"] = self.env.cr.dbname
        image = self.url_open(url.path, params=params)
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers["Content-Type"], "image/png")

    def test_opted_in_test_draft_catalog_image_url_is_usable(self):
        self.product.product_tmpl_id.image_512 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/Hf8AAAAASUVORK5CYII="
        )
        self.connection.write({
            "state": "draft", "environment": "test",
            "allow_draft_api_testing": True,
        })
        self.env.flush_all()
        catalog = self.url_open(
            "/order_connector/catalog",
            params={"db": self.env.cr.dbname, "branch_id": self.basic_config.id},
            headers={"X-Api-Key": "tryorder-secret-test",
                     "X-Odoo-Database": self.env.cr.dbname},
        )
        self.assertEqual(catalog.status_code, 200, catalog.text)
        item = next(row for row in catalog.json()["items"]
                    if row["_id"] == str(self.product.product_tmpl_id.id))
        url = urlsplit(item["image_url"])
        params = {key: values[0] for key, values in parse_qs(url.query).items()}
        params["db"] = self.env.cr.dbname
        image = self.url_open(url.path, params=params)
        self.assertEqual(image.status_code, 200, image.text if image.status_code != 200 else "")
        self.assertTrue(image.headers["Content-Type"].startswith("image/"))
        self.connection.allow_draft_api_testing = False
        self.env.flush_all()
        self.assertEqual(self.url_open(url.path, params=params).status_code, 404)

    def test_signed_jpeg_image_reports_its_actual_mime_type(self):
        image = BytesIO()
        Image.new("RGB", (2, 2), "red").save(image, format="JPEG")
        self.product.product_tmpl_id.image_512 = base64.b64encode(image.getvalue())
        self.env.flush_all()
        catalog = self.url_open(
            "/order_connector/catalog",
            params={"db": self.env.cr.dbname, "branch_id": self.basic_config.id},
            headers={"X-Api-Key": "tryorder-secret-test",
                     "X-Odoo-Database": self.env.cr.dbname},
        )
        self.assertEqual(catalog.status_code, 200, catalog.text)
        item = next(row for row in catalog.json()["items"]
                    if row["_id"] == str(self.product.product_tmpl_id.id))
        url = urlsplit(item["image_url"])
        params = {key: values[0] for key, values in parse_qs(url.query).items()}
        params["db"] = self.env.cr.dbname
        response = self.url_open(url.path, params=params)
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
        self.assertEqual(response.headers["Content-Type"], guess_mimetype(response.content))

    def test_core_adapter_sends_status_to_tryorder_gateway(self):
        self.connection.write({
            "base_url": "https://tryorder.example",
            "access_token": "outbound-secret-test",
        })
        self.env.flush_all()
        created = self._post(order_id="TRY-OUT-1")
        self.assertEqual(created.status_code, 200, created.text)
        order = self._delivery_for_response(created)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"ok": true}'
        with patch(
            "odoo.addons.bs_pos_delivery_tryorder.models.delivery_adapter.requests.post",
            return_value=response,
        ) as post:
            result = self.env["bs.delivery.adapter.tryorder"].update_order(
                self.connection, order.binding_id, "ready",
            )
        self.assertTrue(result["success"])
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://tryorder.example/webhook/odoo")
        self.assertEqual(kwargs["headers"]["X-Webhook-Secret"], "outbound-secret-test")
        self.assertEqual(kwargs["json"]["data"]["provider_order_id"], "TRY-OUT-1")
        self.assertEqual(kwargs["json"]["data"]["order_status"], "ready")

    def test_authenticated_order_enters_core_once_with_original_total(self):
        first = self._post()
        self.assertEqual(first.status_code, 200, first.text)
        core_order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "TRY-HTTP-1"),
        ])
        self.assertEqual(len(core_order), 1)
        self.assertEqual(core_order.amount_total, 20)
        self.assertEqual(core_order.line_ids.price_unit, 10)
        self.assertEqual(self._post().json()["order_id"], first.json()["order_id"])

    def test_draft_test_connection_requires_opt_in_for_ping_and_orders(self):
        self.connection.write({"state": "draft", "environment": "test"})
        self.env.flush_all()
        self.assertEqual(self._post(order_id="TRY-DRAFT-DENIED").status_code, 401)

        self.connection.write({"allow_draft_api_testing": True})
        self.env.flush_all()
        ping = self.url_open(
            "/order_connector/ping",
            params={"db": self.env.cr.dbname},
            headers={
                "X-Api-Key": "tryorder-secret-test",
                "X-Odoo-Database": self.env.cr.dbname,
            },
        )
        self.assertEqual(ping.status_code, 200, ping.text)
        self.assertEqual(self._post("wrong-key").status_code, 401)
        created = self._post(order_id="TRY-DRAFT-ALLOWED")
        self.assertEqual(created.status_code, 200, created.text)
        status = self.url_open(
            f"/order_connector/orders/{created.json()['order_id']}/status",
            method="PUT",
            json={"status": "pending"},
            params={"db": self.env.cr.dbname},
            headers={
                "X-Api-Key": "tryorder-secret-test",
                "X-Odoo-Database": self.env.cr.dbname,
            },
        )
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(self.connection.state, "draft")

    def test_draft_test_opt_in_never_allows_production_or_inactive_connection(self):
        self.connection.provider_id.sudo().write({"implementation_state": "ready"})
        self.connection.write({
            "state": "draft",
            "allow_draft_api_testing": True,
            "environment": "production",
        })
        self.env.flush_all()
        self.assertEqual(self._post(order_id="TRY-PROD-DENIED").status_code, 401)

        self.connection.write({"environment": "test", "active": False})
        self.env.flush_all()
        self.assertEqual(self._post(order_id="TRY-INACTIVE-DENIED").status_code, 401)

        self.connection.write({"active": True, "state": "error"})
        self.env.flush_all()
        self.assertEqual(self._post(order_id="TRY-ERROR-DENIED").status_code, 401)

    def test_opted_in_draft_order_reaches_pos_kitchen_and_board(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Draft Customer"})
        self.env["pos.prep.display"].create({
            "name": "TryOrder Draft Kitchen",
            "company_id": self.basic_config.company_id.id,
            "pos_config_ids": [Command.set(self.basic_config.ids)],
        })
        self.connection.write({
            "state": "draft",
            "environment": "test",
            "allow_draft_api_testing": True,
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()

        response = self._post(order_id="TRY-DRAFT-POS-KITCHEN")
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.state, "accepted", order.pos_processing_error)
        self.assertTrue(order.pos_order_id)
        self.assertEqual(self.env["pos.prep.order"].search_count([
            ("pos_order_id", "=", order.pos_order_id.id),
        ]), 1)
        board = self.env["bs.delivery.order"].get_pos_board_data(
            self.connection.provider_id.id, self.basic_config.id,
        )
        self.assertIn(order.id, [row["id"] for row in board["orders"]])

    def test_create_response_reports_pos_processing_on_exact_replay(self):
        first = self._post(order_id="TRY-PROCESSING-1")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["state"], "received")
        self.assertEqual(first.json()["pos_processing_state"], "pending")
        self.assertFalse(first.json()["pos_order_created"])

        order = self._delivery_for_response(first)
        order._set_pos_processing_result("blocked", "Needs financial review")
        self.env.flush_all()
        replay = self._post(order_id="TRY-PROCESSING-1")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["order_id"], first.json()["order_id"])
        self.assertEqual(replay.json()["pos_processing_state"], "blocked")
        self.assertFalse(replay.json()["pos_order_created"])

    def test_wrong_connection_key_cannot_create_order(self):
        response = self._post("wrong-key")
        self.assertEqual(response.status_code, 401)

    def test_other_connection_cannot_advance_order_status(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Scoped POS"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        other = self.env["bs.delivery.connection"].create({
            "name": "Other TryOrder connection",
            "provider_id": self.connection.provider_id.id,
            "company_id": self.basic_config.company_id.id,
            "state": "connected",
            "auth_scheme": "api_key_secret",
            "webhook_secret": "other-connection-secret",
            "pos_config_ids": [Command.set(self.basic_config.ids)],
        })
        self.env["bs.delivery.store.binding"].create({
            "connection_id": other.id,
            "pos_config_id": self.basic_config.id,
            "remote_store_id": "other-store",
        })
        self.env.flush_all()
        created = self._post(order_id="TRY-SCOPE-1")
        self.assertEqual(created.status_code, 200, created.text)
        response = self.url_open(
            f"/order_connector/orders/{created.json()['order_id']}/status",
            method="PUT", json={"status": "accepted"},
            params={"db": self.env.cr.dbname},
            headers={"X-Api-Key": "other-connection-secret",
                     "X-Odoo-Database": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 404)
        order = self._delivery_for_response(created)
        self.assertEqual(order.state, "accepted")
        self.assertEqual(created.json()["order_id"], str(order.pos_order_id.id))

    def test_pending_core_status_ignores_unrelated_pos_id_collision(self):
        partner = self.env["res.partner"].create({"name": "TryOrder Pending Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        created = self._post(order_id="TRY-CORE-ID-COLLISION")
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["kind"], "core")
        core_id = int(created.json()["order_id"])
        session = self.env["pos.session"].create({
            "config_id": self.basic_config.id,
        })
        unrelated_pos = self.env["pos.order"].create({
            "session_id": session.id,
            "company_id": self.basic_config.company_id.id,
            "pricelist_id": self.basic_config.pricelist_id.id,
            "amount_total": 0.0,
            "amount_tax": 0.0,
            "amount_paid": 0.0,
            "amount_return": 0.0,
        })
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE pos_order SET id = %s WHERE id = %s",
            (core_id, unrelated_pos.id),
        )
        self.env.invalidate_all()
        self.assertTrue(self.env["pos.order"].browse(core_id).exists())
        status = self.url_open(
            f"/order_connector/orders/{core_id}/status",
            method="PUT", json={"status": "preparing"},
            params={"db": self.env.cr.dbname},
            headers={"X-Api-Key": "tryorder-secret-test",
                     "X-Odoo-Database": self.env.cr.dbname},
        )
        self.assertEqual(status.status_code, 200, status.text)
        core = self.env["bs.delivery.order"].browse(core_id)
        self.assertEqual(core.state, "preparing")
        self.assertFalse(core.pos_order_id)

    def test_branches_only_returns_this_connections_pos(self):
        response = self.url_open(
            "/order_connector/branches",
            params={"db": self.env.cr.dbname},
            headers={
                "X-Api-Key": "tryorder-secret-test",
                "X-Odoo-Database": self.env.cr.dbname,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            {row["id"] for row in response.json()["branches"]},
            {str(self.basic_config.id)},
        )

    def test_catalog_returns_native_modifier_option_identity(self):
        extra = self.env["product.product"].create({
            "name": "Catalog Cheese", "available_in_pos": True,
            "list_price": 5, "taxes_id": [Command.clear()],
        })
        group = self.env["pos.modifier.group"].create({
            "name": "Catalog Extras", "max_qty": 2,
            "company_id": self.basic_config.company_id.id,
        })
        option = self.env["pos.modifier.option"].create({
            "name": "Catalog Cheese", "group_id": group.id,
            "option_type": "product", "operation": "add",
            "product_id": extra.id, "price_mode": "fixed_extra", "extra_price": 5,
        })
        self.product.product_tmpl_id.write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": group.id})],
        })
        self.env.flush_all()
        response = self.url_open(
            "/order_connector/catalog",
            params={"db": self.env.cr.dbname, "branch_id": self.basic_config.id},
            headers={
                "X-Api-Key": "tryorder-secret-test",
                "X-Odoo-Database": self.env.cr.dbname,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        menu_item = next(row for row in response.json()["items"]
                         if row["_id"] == str(self.product.product_tmpl_id.id))
        group_line = self.product.product_tmpl_id.modifier_group_line_ids
        self.assertIn(
            f"posmod-{option.id}-{group_line.id}",
            [row["modifier"]["_id"] for row in menu_item["add_on"]],
        )

    def test_cash_is_allocated_as_expected_collection(self):
        response = self._post(order_id="TRY-CASH-1")
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(len(order.payment_allocation_ids), 1)
        allocation = order.payment_allocation_ids
        self.assertEqual(allocation.amount, 20)
        self.assertEqual(allocation.external_payment_code, "cash")
        self.assertEqual(allocation.collection_status, "expected")
        self.assertEqual(allocation.collected_by, "restaurant")

    def test_online_payment_is_collected_and_reaches_pos(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Online Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        response = self._post(order_id="TRY-CARD-1", overrides={"payment_type": "online"})
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertTrue(order.pos_order_id, order.pos_processing_error)
        self.assertEqual(order.payment_allocation_ids.collection_status, "collected")
        self.assertEqual(order.payment_allocation_ids.external_payment_code, "card")
        self.assertEqual(order.pos_order_id.amount_paid, 20)

    def test_accepted_order_reaches_pos_and_core_board(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Invoice Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        response = self._post(order_id="TRY-POS-1")
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.state, "accepted")
        self.assertTrue(order.pos_order_id, order.pos_processing_error)
        self.assertEqual(order.pos_order_id.config_id, self.basic_config)
        self.assertEqual(order.pos_order_id.lines.product_id, self.product)
        board = self.env["bs.delivery.order"].get_pos_board_data(
            self.connection.provider_id.id, self.basic_config.id,
        )
        self.assertIn(order.id, [row["id"] for row in board["orders"]])

    def test_accepted_order_reaches_kitchen_once(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Kitchen Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env["pos.prep.display"].create({
            "name": "TryOrder Kitchen",
            "company_id": self.basic_config.company_id.id,
            "pos_config_ids": [Command.set(self.basic_config.ids)],
        })
        self.env.flush_all()
        first = self._post(order_id="TRY-KITCHEN-1")
        self.assertEqual(first.status_code, 200, first.text)
        order = self._delivery_for_response(first)
        prep = self.env["pos.prep.order"].search([
            ("pos_order_id", "=", order.pos_order_id.id),
        ])
        self.assertEqual(len(prep), 1)
        self.assertTrue(prep.prep_line_ids)
        self.assertEqual(self._post(order_id="TRY-KITCHEN-1").status_code, 200)
        self.assertEqual(self.env["pos.prep.order"].search_count([
            ("pos_order_id", "=", order.pos_order_id.id),
        ]), 1)

    def test_status_update_advances_the_connection_scoped_core_order(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Status Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        created = self._post(order_id="TRY-STATUS-1")
        self.assertEqual(created.status_code, 200, created.text)
        response = self.url_open(
            f"/order_connector/orders/{created.json()['order_id']}/status",
            method="PUT",
            json={"status": "preparing"},
            params={"db": self.env.cr.dbname},
            headers={
                "X-Api-Key": "tryorder-secret-test",
                "X-Odoo-Database": self.env.cr.dbname,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(created)
        self.assertEqual(order.state, "preparing")
        self.assertEqual(order.pos_processing_state, "done")
        self.assertEqual(response.json()["order_id"], str(order.pos_order_id.id))

    def test_replayed_status_does_not_create_a_new_transition(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Replay Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        created = self._post(order_id="TRY-REPLAY-STATUS-1")
        self.assertEqual(created.status_code, 200, created.text)
        order_id = int(created.json()["order_id"])
        def send_status():
            return self.url_open(
                f"/order_connector/orders/{order_id}/status",
                method="PUT", json={"status": "accepted"},
                params={"db": self.env.cr.dbname},
                headers={"X-Api-Key": "tryorder-secret-test",
                         "X-Odoo-Database": self.env.cr.dbname},
            )
        first = send_status()
        self.assertEqual(first.status_code, 200, first.text)
        order = self._delivery_for_response(created)
        first_update_at = order.core_last_provider_update_at
        first_transition_count = len(order.transition_ids)
        replay = send_status()
        self.assertEqual(replay.status_code, 200, replay.text)
        order.invalidate_recordset()
        self.assertEqual(order.core_last_provider_update_at, first_update_at)
        self.assertEqual(len(order.transition_ids), first_transition_count)

    def test_auto_print_exposes_one_kitchen_job_after_acceptance(self):
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Print Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
            "auto_print_kitchen": True,
        })
        self.env.flush_all()
        first = self._post(order_id="TRY-PRINT-1")
        self.assertEqual(first.status_code, 200, first.text)
        order = self._delivery_for_response(first)
        order_id = order.id
        self.assertEqual(order.kitchen_print_state, "pending")
        order._with_delivery_internal_update().write({
            "kitchen_print_last_attempt_at": fields.Datetime.subtract(
                fields.Datetime.now(), seconds=31,
            ),
        })
        jobs = self.env["bs.delivery.order"].get_pending_kitchen_prints(self.basic_config.id)
        self.assertEqual([job["order_id"] for job in jobs if job["order_id"] == order_id],
                         [order_id])
        self.assertEqual(self._post(order_id="TRY-PRINT-1").status_code, 200)
        order._with_delivery_internal_update().write({
            "kitchen_print_last_attempt_at": fields.Datetime.subtract(
                fields.Datetime.now(), seconds=31,
            ),
        })
        jobs = self.env["bs.delivery.order"].get_pending_kitchen_prints(self.basic_config.id)
        self.assertEqual(sum(job["order_id"] == order_id for job in jobs), 1)

    def test_modifier_is_child_line_without_changing_product_price(self):
        extra = self.env["product.product"].create({
            "name": "Extra Cheese", "available_in_pos": True,
            "list_price": 5, "taxes_id": [Command.clear()],
        })
        group = self.env["pos.modifier.group"].create({
            "name": "Extras", "selection_type": "multiple",
            "max_qty": 3, "company_id": self.basic_config.company_id.id,
        })
        option = self.env["pos.modifier.option"].create({
            "name": "Extra Cheese", "group_id": group.id,
            "option_type": "product", "operation": "add",
            "product_id": extra.id, "price_mode": "fixed_extra",
            "extra_price": 5, "max_qty": 3,
        })
        self.product.product_tmpl_id.write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": group.id})],
        })
        self.env.flush_all()
        response = self._post(
            order_id="TRY-MOD-1",
            modifiers=[{"id": f"posmod-{option.id}", "qty": 1, "price": 5}],
        )
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.amount_total, 30)
        self.assertEqual(len(order.line_ids), 2)
        parent = order.line_ids.filtered(lambda line: not line.is_modifier)
        child = order.line_ids - parent
        self.assertEqual(parent.price_unit, 10)
        self.assertEqual(child.parent_line_id, parent)
        self.assertEqual(child.quantity, 2)
        self.assertEqual(child.price_unit, 5)
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Modifier Customer"})
        order._set_delivery_invoice_partner(partner.id)
        order.transition_to(
            "accepted", source="manual", expected_state="received",
            idempotency_key="accept:TRY-MOD-1",
        )
        self.assertTrue(order.pos_order_id, order.pos_processing_error)
        self.assertEqual(order.pos_order_id.amount_total, 30)
        modifier_line = order.pos_order_id.lines.filtered("is_modifier_line")
        self.assertEqual(modifier_line.modifier_option_id, option)
        self.assertEqual(modifier_line.modifier_parent_line_id.product_id, self.product)

    def test_modifier_uses_catalog_menu_pricelist_during_pos_validation(self):
        extra = self.env["product.product"].create({
            "name": "Jalapeno", "available_in_pos": True,
            "list_price": 3, "taxes_id": [Command.clear()],
        })
        group = self.env["pos.modifier.group"].create({
            "name": "Toppings", "selection_type": "multiple",
            "max_qty": 3, "company_id": self.basic_config.company_id.id,
        })
        option = self.env["pos.modifier.option"].create({
            "name": "Jalapeno", "group_id": group.id,
            "option_type": "product", "operation": "add",
            "product_id": extra.id, "price_mode": "product_pricelist",
        })
        self.product.product_tmpl_id.write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": group.id})],
        })
        group_line = self.product.product_tmpl_id.modifier_group_line_ids
        menu_pricelist = self.env["product.pricelist"].create({
            "name": "TryOrder menu", "currency_id": self.basic_config.company_id.currency_id.id,
            "company_id": self.basic_config.company_id.id,
            "item_ids": [Command.create({
                "applied_on": "0_product_variant", "product_id": extra.id,
                "compute_price": "fixed", "fixed_price": 1,
            })],
        })
        branch_pricelist = self.env["product.pricelist"].create({
            "name": "POS branch", "currency_id": self.basic_config.company_id.currency_id.id,
            "company_id": self.basic_config.company_id.id,
            "item_ids": [Command.create({
                "applied_on": "0_product_variant", "product_id": extra.id,
                "compute_price": "fixed", "fixed_price": 3,
            })],
        })
        self.connection.menu_pricelist_id = menu_pricelist
        self.store.pricelist_id = branch_pricelist
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Price Customer"})
        self.env.flush_all()

        response = self._post(
            order_id="TRY-MENU-PRICE-1",
            modifiers=[{"id": f"posmod-{option.id}-{group_line.id}", "qty": 1, "price": 1}],
        )
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        order._set_delivery_invoice_partner(partner.id)
        order.transition_to("accepted", source="webhook")
        self.assertEqual(order.pos_processing_state, "done", order.pos_processing_error)
        self.assertEqual(order.pos_order_id.pricelist_id, menu_pricelist)
        self.assertEqual(order.pos_order_id.lines.filtered("is_modifier_line").price_unit, 1)

        stale = self._post(
            order_id="TRY-MENU-PRICE-STALE",
            modifiers=[{"id": f"posmod-{option.id}-{group_line.id}", "qty": 1, "price": 2}],
        )
        self.assertEqual(stale.status_code, 200, stale.text)
        stale_order = self._delivery_for_response(stale)
        stale_order._set_delivery_invoice_partner(partner.id)
        with self.assertRaisesRegex(UserError, "Modifier price mismatch"), self.env.cr.savepoint():
            stale_order.transition_to("accepted", source="webhook")
        self.assertFalse(stale_order.pos_order_id)

    def test_per_product_modifier_id_links_exact_option(self):
        extra = self.env["product.product"].create({
            "name": "Per-product Cheese", "available_in_pos": True,
            "list_price": 5, "taxes_id": [Command.clear()],
        })
        group = self.env["pos.modifier.group"].create({
            "name": "Per-product Extras", "selection_type": "multiple",
            "max_qty": 3, "company_id": self.basic_config.company_id.id,
        })
        option = self.env["pos.modifier.option"].create({
            "name": "Per-product Cheese", "group_id": group.id,
            "option_type": "product", "operation": "add",
            "product_id": extra.id, "price_mode": "fixed_extra",
            "extra_price": 5, "max_qty": 3,
        })
        self.product.product_tmpl_id.write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": group.id})],
        })
        group_line = self.product.product_tmpl_id.modifier_group_line_ids
        self.env.flush_all()
        response = self._post(
            order_id="TRY-MOD-LINE",
            modifiers=[{"id": f"posmod-{option.id}-{group_line.id}", "qty": 1, "price": 5}],
        )
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        child = order.line_ids.filtered("is_modifier")
        self.assertEqual(child.external_product_id, f"posmod-{option.id}")
        # A group line of another product is not accepted for this product.
        response = self._post(
            order_id="TRY-MOD-LINE-WRONG",
            modifiers=[{"id": f"posmod-{option.id}-{group_line.id + 999999}", "qty": 1, "price": 5}],
        )
        self.assertNotEqual(response.status_code, 200, response.text)
        self.assertIn("not linked to this product", response.text)

    def test_instruction_modifier_reaches_pos_without_fake_product(self):
        group = self.env["pos.modifier.group"].create({
            "name": "Cooking Instructions", "selection_type": "multiple",
            "max_qty": 2, "company_id": self.basic_config.company_id.id,
        })
        option = self.env["pos.modifier.option"].create({
            "name": "No onions", "group_id": group.id,
            "option_type": "instruction", "operation": "remove",
        })
        self.product.product_tmpl_id.write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [Command.create({"modifier_group_id": group.id})],
        })
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Instruction Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env["pos.prep.display"].create({
            "name": "TryOrder Instruction Kitchen",
            "company_id": self.basic_config.company_id.id,
            "pos_config_ids": [Command.set(self.basic_config.ids)],
        })
        self.env.flush_all()
        response = self._post(
            order_id="TRY-INSTR-1",
            modifiers=[{"id": f"posmod-{option.id}", "qty": 1, "price": 0}],
        )
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.amount_total, 20)
        self.assertTrue(order.pos_order_id, order.pos_processing_error)
        self.assertEqual(len(order.pos_order_id.lines), 1)
        parent = order.pos_order_id.lines
        self.assertEqual(parent.modifier_selection_data[str(option.id)], 1)
        self.assertEqual(parent.modifier_instruction_data["instructions"][0]["option_id"],
                         option.id)
        prep = self.env["pos.prep.order"].search([
            ("pos_order_id", "=", order.pos_order_id.id),
        ])
        self.assertEqual(len(prep), 1)
        self.assertIn(
            option.id,
            [entry.get("option_id") for line in prep.prep_line_ids
             for entry in line.modifier_display_data or []],
        )

    def test_selected_modifier_id_wins_when_options_share_product(self):
        extra = self.env["product.product"].create({
            "name": "Shared Topping", "available_in_pos": True,
            "list_price": 2, "taxes_id": [Command.clear()],
        })
        group = self.env["pos.modifier.group"].create({
            "name": "Shared Options", "selection_type": "multiple",
            "max_qty": 2, "company_id": self.basic_config.company_id.id,
        })
        first = self.env["pos.modifier.option"].create({
            "name": "First", "group_id": group.id, "option_type": "product",
            "operation": "add", "product_id": extra.id,
            "price_mode": "fixed_extra", "extra_price": 2,
        })
        second_group = self.env["pos.modifier.group"].create({
            "name": "Other Shared Options", "selection_type": "multiple",
            "max_qty": 2, "company_id": self.basic_config.company_id.id,
        })
        chosen = first.copy({"name": "Chosen", "group_id": second_group.id})
        self.product.product_tmpl_id.write({
            "allow_modifiers": True,
            "modifier_group_line_ids": [
                Command.create({"modifier_group_id": group.id}),
                Command.create({"modifier_group_id": second_group.id}),
            ],
        })
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Option Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        response = self._post(order_id="TRY-CHOICE-1", modifiers=[{
            "id": f"posmod-{chosen.id}", "qty": 1, "price": 2,
        }])
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertTrue(order.pos_order_id, order.pos_processing_error)
        child = order.pos_order_id.lines.filtered("is_modifier_line")
        self.assertEqual(child.modifier_option_id, chosen)

    def test_combo_choice_links_to_native_pos_combo_line(self):
        side = self.env["product.product"].create({
            "name": "TryOrder Combo Side", "available_in_pos": True,
            "list_price": 3, "taxes_id": [Command.clear()],
        })
        combo = self.env["product.combo"].create({
            "name": "TryOrder Combo Choice",
            "combo_item_ids": [Command.create({
                "product_id": side.id, "extra_price": 3,
            })],
        })
        parent_product = self.env["product.product"].create({
            "name": "TryOrder Combo Meal", "type": "combo",
            "available_in_pos": True, "list_price": 10,
            "taxes_id": [Command.clear()],
            "combo_ids": [Command.link(combo.id)],
        })
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Combo Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        response = self._post(order_id="TRY-COMBO-1", overrides={
            "items": [{
                "product_id": parent_product.product_tmpl_id.id,
                "qty": 2, "price": 10,
                "modifiers": [{
                    "id": f"comboitem-{combo.combo_item_ids.id}",
                    "qty": 1, "price": 3,
                }],
            }],
        })
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.amount_total, 26)
        self.assertTrue(order.pos_order_id, order.pos_processing_error)
        child = order.pos_order_id.lines.filtered(lambda line: line.product_id == side)
        self.assertEqual(child.combo_item_id, combo.combo_item_ids)
        self.assertEqual(child.combo_parent_id.product_id, parent_product)

    def test_untaxed_combo_with_taxed_included_choice_reaches_pos(self):
        tax = self.env["account.tax"].create({
            "name": "TryOrder Combo Choice 15%", "amount": 15,
            "amount_type": "percent", "type_tax_use": "sale",
            "company_id": self.basic_config.company_id.id,
        })
        choice = self.env["product.product"].create({
            "name": "Taxed Included Choice", "available_in_pos": True,
            "list_price": 0, "taxes_id": [Command.set(tax.ids)],
        })
        combo = self.env["product.combo"].create({
            "name": "Included Taxed Choice",
            "combo_item_ids": [Command.create({
                "product_id": choice.id, "extra_price": 0,
            })],
        })
        parent = self.env["product.product"].create({
            "name": "Untaxed Combo", "type": "combo",
            "available_in_pos": True, "list_price": 10,
            "taxes_id": [Command.clear()],
            "combo_ids": [Command.link(combo.id)],
        })
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "Combo Tax Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()

        response = self._post(order_id="TRY-COMBO-MIXED-TAX-1", overrides={
            "total": 10,
            "items": [{
                "product_id": parent.product_tmpl_id.id,
                "qty": 1, "price": 10,
                "modifiers": [{
                    "id": f"comboitem-{combo.combo_item_ids.id}",
                    "qty": 1, "price": 0,
                }],
            }],
        })
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.state, "accepted", order.pos_processing_error)
        self.assertEqual(order.items_subtotal, 8.7)
        self.assertEqual(order.vat_amount, 1.3)
        self.assertEqual(order.amount_total, 10)
        self.assertTrue(order.pos_order_id, order.pos_processing_error)
        self.assertEqual(order.pos_order_id.amount_total, 10)
        self.assertEqual(order.pos_order_id.amount_tax, 1.3)
        self.assertEqual(order.pos_order_id.account_move.amount_total, 10)
        self.assertEqual(order.pos_processing_state, "done")
        self.assertEqual(response.json()["order_id"], str(order.pos_order_id.id))

    def test_combo_allocates_base_across_mixed_tax_choices_and_quantity(self):
        tax = self.env["account.tax"].create({
            "name": "TryOrder Mixed Combo 15%", "amount": 15,
            "amount_type": "percent", "type_tax_use": "sale",
            "company_id": self.basic_config.company_id.id,
        })
        taxed = self.env["product.product"].create({
            "name": "Taxed Combo Choice", "available_in_pos": True,
            "taxes_id": [Command.set(tax.ids)],
        })
        untaxed = self.env["product.product"].create({
            "name": "Untaxed Combo Choice", "available_in_pos": True,
            "taxes_id": [Command.clear()],
        })
        taxed_group = self.env["product.combo"].create({
            "name": "Taxed Choice Group",
            "combo_item_ids": [Command.create({"product_id": taxed.id})],
        })
        untaxed_group = self.env["product.combo"].create({
            "name": "Untaxed Choice Group",
            "combo_item_ids": [Command.create({"product_id": untaxed.id})],
        })
        parent = self.env["product.product"].create({
            "name": "Mixed Tax Combo", "type": "combo",
            "available_in_pos": True, "list_price": 10,
            "taxes_id": [Command.clear()],
            "combo_ids": [Command.link(taxed_group.id), Command.link(untaxed_group.id)],
        })
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "Mixed Combo Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()

        response = self._post(order_id="TRY-COMBO-TWO-CHOICES-1", overrides={
            "total": 20,
            "items": [{
                "product_id": parent.product_tmpl_id.id,
                "qty": 2, "price": 10,
                "modifiers": [
                    {"id": f"comboitem-{taxed_group.combo_item_ids.id}",
                     "qty": 1, "price": 0},
                    {"id": f"comboitem-{untaxed_group.combo_item_ids.id}",
                     "qty": 1, "price": 0},
                ],
            }],
        })
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.state, "accepted", order.pos_processing_error)
        self.assertEqual(order.amount_total, 20)
        self.assertEqual(order.vat_amount, 1.3)
        self.assertEqual(order.pos_order_id.amount_total, 20)
        self.assertEqual(order.pos_order_id.amount_tax, 1.3)
        self.assertEqual(order.pos_order_id.account_move.amount_total, 20)
        self.assertEqual(len(order.pos_order_id.lines.filtered("combo_parent_id")), 2)

    def test_tax_included_combo_keeps_invoice_total(self):
        tax = self.env["account.tax"].create({
            "name": "TryOrder Combo Included Tax", "amount": 5,
            "amount_type": "percent", "type_tax_use": "sale",
            "price_include_override": "tax_included",
            "company_id": self.basic_config.company_id.id,
        })
        side = self.env["product.product"].create({
            "name": "Included Combo Side", "available_in_pos": True,
            "list_price": 3, "taxes_id": [Command.set(tax.ids)],
        })
        combo = self.env["product.combo"].create({
            "name": "Included Combo Choice",
            "combo_item_ids": [Command.create({
                "product_id": side.id, "extra_price": 3,
            })],
        })
        parent_product = self.env["product.product"].create({
            "name": "Included Combo Meal", "type": "combo",
            "available_in_pos": True, "list_price": 10,
            "taxes_id": [Command.set(tax.ids)],
            "combo_ids": [Command.link(combo.id)],
        })
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Combo Tax Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        response = self._post(order_id="TRY-COMBO-TAX-1", overrides={
            "total": 26,
            "items": [{
                "product_id": parent_product.product_tmpl_id.id,
                "qty": 2, "price": 10,
                "modifiers": [{
                    "id": f"comboitem-{combo.combo_item_ids.id}",
                    "qty": 1, "price": 3,
                }],
            }],
        })
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertTrue(order.pos_order_id, order.pos_processing_error)
        self.assertEqual(order.amount_total, 26)
        self.assertEqual(order.pos_order_id.amount_total, 26)
        self.assertEqual(order.pos_order_id.account_move.amount_total, 26)

    def test_taxed_product_preserves_core_pos_and_invoice_totals(self):
        tax = self.env["account.tax"].create({
            "name": "TryOrder 5% Tax", "amount": 5,
            "amount_type": "percent", "type_tax_use": "sale",
            "company_id": self.basic_config.company_id.id,
        })
        self.product.taxes_id = [Command.set(tax.ids)]
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Tax Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        response = self._post(order_id="TRY-TAX-1", overrides={"total": 21})
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.items_subtotal, 20)
        self.assertEqual(order.vat_amount, 1)
        self.assertEqual(order.amount_total, 21)
        self.assertTrue(order.pos_order_id, order.pos_processing_error)
        self.assertEqual(order.pos_order_id.amount_total, 21)
        self.assertEqual(order.pos_order_id.amount_tax, 1)
        self.assertEqual(order.pos_order_id.account_move.amount_total, 21)

    def test_tax_included_price_preserves_provider_total(self):
        tax = self.env["account.tax"].create({
            "name": "TryOrder Included 5%", "amount": 5,
            "amount_type": "percent", "type_tax_use": "sale",
            "price_include_override": "tax_included",
            "company_id": self.basic_config.company_id.id,
        })
        self.product.taxes_id = [Command.set(tax.ids)]
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Included Tax Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        response = self._post(order_id="TRY-INCLUDED-TAX-1", overrides={"total": 20})
        self.assertEqual(response.status_code, 200, response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.amount_total, 20)
        self.assertTrue(order.pos_order_id, order.pos_processing_error)
        self.assertEqual(order.pos_order_id.amount_total, 20)
        self.assertEqual(order.pos_order_id.account_move.amount_total, 20)

    def test_tax_included_eleven_at_fifteen_percent_reaches_pos_and_invoice(self):
        tax = self.env["account.tax"].create({
            "name": "TryOrder Included 15%", "amount": 15,
            "amount_type": "percent", "type_tax_use": "sale",
            "price_include_override": "tax_included",
            "company_id": self.basic_config.company_id.id,
        })
        self.product.write({
            "list_price": 11,
            "taxes_id": [Command.set(tax.ids)],
        })
        self.basic_config.open_ui()
        partner = self.env["res.partner"].create({"name": "TryOrder Rounding Customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })
        self.env.flush_all()
        response = self._post(order_id="TRY-INCLUDED-15-11", overrides={
            "items": [{
                "product_id": self.product.product_tmpl_id.id,
                "qty": 1,
                "price": 11,
            }],
            "total": 11,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["kind"], "pos", response.text)
        order = self._delivery_for_response(response)
        self.assertEqual(order.items_subtotal, 9.57)
        self.assertEqual(order.vat_amount, 1.43)
        self.assertEqual(order.amount_total, 11)
        self.assertEqual(order.pos_order_id.amount_total, 11)
        self.assertEqual(order.pos_order_id.amount_tax, 1.43)
        self.assertEqual(order.pos_order_id.account_move.amount_total, 11)
        quantity_response = self._post(order_id="TRY-INCLUDED-15-11-Q2", overrides={
            "items": [{
                "product_id": self.product.product_tmpl_id.id,
                "qty": 2,
                "price": 11,
            }],
            "total": 22,
        })
        self.assertEqual(quantity_response.status_code, 200, quantity_response.text)
        quantity_order = self._delivery_for_response(quantity_response)
        self.assertEqual(quantity_order.amount_total, 22)
        self.assertEqual(quantity_order.pos_order_id.amount_total, 22)
        self.assertEqual(quantity_order.pos_order_id.account_move.amount_total, 22)
        self.assertEqual(order.pos_order_id.account_move.amount_tax, 1.43)
