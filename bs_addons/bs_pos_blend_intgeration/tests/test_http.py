import copy
import json
import uuid
from unittest.mock import patch

from odoo.tests.common import HttpCase, new_test_user, tagged

from ..services.menu import BlendMenuService
from ..services import outbound
from ..services.stock_availability import BlendStockAvailabilityService
from .common import sample_menu, configure_blend_preset
from .test_outbound import FakeResponse


@tagged("post_install", "-at_install")
class TestBlendWebhookHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("ar_001")
        cls.env.company.partner_id.tz = "UTC"
        cls.token = "http-token-test"
        suffix = uuid.uuid4().hex[:10]
        cls.login = "pos-user-%s@example.com" % suffix
        cls.password = "Blend-Test-%s!" % suffix
        cls.login_user = new_test_user(
            cls.env,
            login=cls.login,
            password=cls.password,
            groups="base.group_portal",
        )
        cls.config = cls.env["pos.config"].create(
            {
                "name": "Blend HTTP Test POS",
                "blend_enabled": True,
                "blend_branch_id": "http-test-branch-%s" % suffix,
                "blend_brand_id": "http-test-brand-%s" % suffix,
                "blend_inbound_token": cls.token,
                "blend_login_user_id": cls.login_user.id,
                "payment_method_ids": [(6, 0, [])],
            }
        )
        configure_blend_preset(cls.config)
        BlendMenuService(cls.env, cls.config).sync_menu(
            sample_menu(cls.config.blend_branch_id)
        )
        categories = cls.env["pos.category"].search([("blend_config_id", "=", cls.config.id)])
        cls.config.write({"limit_categories": True, "iface_available_categ_ids": [(6, 0, categories.ids)]})
        cls.pos_session = cls.env["pos.session"].create({"config_id": cls.config.id})
        cls.pos_session.set_opening_control(0, "")

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % self.token,
            "X-Odoo-Database": self.env.cr.dbname,
        }

    def _login(self, password=None, data=None):
        payload = data if data is not None else {
            "email": self.login,
            "password": self.password if password is None else password,
        }
        return self.url_open(
            "/api/login",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Odoo-Database": self.env.cr.dbname,
            },
            params={"db": self.env.cr.dbname},
        )

    def _api_headers(self, token=None):
        return {
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % (token or self.token),
            "X-Odoo-Database": self.env.cr.dbname,
            "X-Request-ID": uuid.uuid4().hex,
        }

    def _create_order_payload(self, partner_reference=None):
        aggregator_order_id = str(uuid.uuid4().int % 10_000_000_000)
        return {
            "branch_id": self.config.blend_branch_id,
            "brand_id": self.config.blend_brand_id,
            "partner_reference": partner_reference
            or "Jahez: %s" % aggregator_order_id,
            "order_type": "delivery",
            "aggregator": "Jahez",
            "aggregator_order_id": aggregator_order_id,
            "note": "cut in half please",
            "total": 103.5,
            "subtotal": 100.0,
            "tax": 13.5,
            "status": "PENDING",
            "customer": {"phone": "0122222", "name": "Blend POS Customer"},
            "products": [
                {
                    "id": "100",
                    "quantity": 2,
                    "total": 95.5,
                    "note": None,
                    "modifiers": [
                        {
                            "id": "200",
                            "quantity": 2,
                            "total": 4.0,
                            "note": "Add cheese",
                        }
                    ],
                }
            ],
        }

    def test_login_route_contract(self):
        rule = next(
            rule
            for rule in self.env["ir.http"].routing_map().iter_rules()
            if str(rule) == "/api/login"
        )
        routing = rule.endpoint.routing
        self.assertEqual(set(routing["methods"]), {"POST"})
        self.assertEqual(routing["auth"], "public")
        self.assertEqual(routing["type"], "http")
        self.assertFalse(routing["csrf"])
        self.assertFalse(routing["save_session"])

    def test_login_returns_access_token_accepted_by_webhook(self):
        response = self._login()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        access_token = response.json()["access_token"]
        self.assertTrue(access_token)

        payload = {
            "event": "branch_availability",
            "event_time": "2026-09-01T13:05:00Z",
            "branch_id": self.config.blend_branch_id,
            "payload": {
                "branch_id": self.config.blend_branch_id,
                "is_available": True,
            },
        }
        webhook = self.url_open(
            "/api/blend/webhook",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % access_token,
                "X-Odoo-Database": self.env.cr.dbname,
            },
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(webhook.status_code, 200, webhook.text)
        self.assertEqual(webhook.json()["status"], "success")

    def test_login_rejects_wrong_credentials(self):
        response = self._login(password="definitely-wrong")
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json(), {"status": "Credentials do not match"})

    def test_login_rejects_invalid_json(self):
        response = self.url_open(
            "/api/login",
            data=b"{not-json",
            headers={
                "Content-Type": "application/json",
                "X-Odoo-Database": self.env.cr.dbname,
            },
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json(), {"status": "Invalid JSON body"})

    def test_login_rotates_previous_access_token(self):
        first_token = self._login().json()["access_token"]
        second_token = self._login().json()["access_token"]
        self.assertNotEqual(first_token, second_token)
        self.config.invalidate_recordset()
        self.assertFalse(self.config._blend_check_inbound_token(first_token))
        self.assertTrue(self.config._blend_check_inbound_token(second_token))

    def test_disabling_integration_revokes_login_token(self):
        access_token = self._login().json()["access_token"]
        self.assertTrue(self.config._blend_check_inbound_token(access_token))
        self.config.write({"blend_enabled": False})
        self.assertFalse(self.config._blend_check_inbound_token(access_token))

    def test_portal_cannot_revoke_login_token_over_rpc(self):
        access_token = self._login().json()["access_token"]
        self.authenticate(self.login, self.password)
        for endpoint in ("/web/dataset/call_button", "/web/dataset/call_kw"):
            with self.subTest(endpoint=endpoint):
                response = self.url_open(
                    endpoint,
                    data=json.dumps({
                        "jsonrpc": "2.0", "method": "call", "id": 1,
                        "params": {
                            "model": "pos.config", "method": "action_revoke_blend_login_token",
                            "args": [self.config.ids], "kwargs": {},
                        },
                    }),
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(response.json().get("error", {}).get("data", {}).get("name"),
                                 "odoo.exceptions.AccessError", response.text)
                self.config.invalidate_recordset()
                self.assertTrue(self.config._blend_check_inbound_token(access_token))
        branches = self.url_open(
            "/api/branches", headers=self._api_headers(access_token),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(branches.status_code, 200, branches.text)

    def test_webhook_route_contract(self):
        rule = next(
            rule
            for rule in self.env["ir.http"].routing_map().iter_rules()
            if str(rule) == "/api/blend/webhook"
        )
        routing = rule.endpoint.routing
        self.assertEqual(set(routing["methods"]), {"POST"})
        self.assertEqual(routing["auth"], "public")
        self.assertEqual(routing["type"], "http")
        self.assertFalse(routing["csrf"])
        self.assertFalse(routing["save_session"])

    def test_manual_pos_routes_match_blend_contract(self):
        expected = {
            "/api/menu": {"GET"},
            "/api/branches": {"GET"},
            "/api/orders": {"POST"},
            "/api/order/update": {"PUT"},
        }
        routes = {
            str(rule): rule.endpoint.routing
            for rule in self.env["ir.http"].routing_map().iter_rules()
            if str(rule) in expected
        }
        self.assertEqual(set(routes), set(expected))
        for path, methods in expected.items():
            self.assertEqual(set(routes[path]["methods"]), methods)
            self.assertEqual(routes[path]["auth"], "public")
            self.assertEqual(routes[path]["type"], "http")
            self.assertFalse(routes[path]["csrf"])
            self.assertFalse(routes[path]["save_session"])

    def test_get_branches_requires_bearer_and_returns_documented_shape(self):
        unauthorized = self.url_open(
            "/api/branches",
            headers={"X-Odoo-Database": self.env.cr.dbname},
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(unauthorized.status_code, 401, unauthorized.text)

        response = self.url_open(
            "/api/branches",
            headers=self._api_headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"branches"})
        branch = next(
            item
            for item in body["branches"]
            if item["id"] == self.config.blend_branch_id
        )
        self.assertEqual(
            set(branch), {"id", "name", "status", "brand_id", "location"}
        )
        self.assertEqual(branch["brand_id"], self.config.blend_brand_id)
        self.assertIs(branch["status"], True)

    def test_get_menu_returns_documented_shape_and_filters(self):
        response = self.url_open(
            "/api/menu",
            headers=self._api_headers(),
            params={
                "db": self.env.cr.dbname,
                "page": 1,
                "per": 25,
                "filter['product_id']": "100",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"categories", "pagination"})
        self.assertTrue(body["categories"])
        self.assertEqual(body["categories"][0]["id"], 10)
        product = body["categories"][0]["products"][0]
        self.assertEqual(product["id"], 100)
        self.assertEqual(
            set(product),
            {
                "id",
                "name",
                "description",
                "stock",
                "status",
                "price",
                "image_url",
                "available_branches",
                "modifier_groups",
            },
        )
        self.assertEqual(product["modifier_groups"][0]["id"], 20)
        self.assertEqual(product["modifier_groups"][0]["max_selection"], 2)
        self.assertGreater(product["modifier_groups"][0]["max_selection"], 0)
        self.assertEqual(
            product["modifier_groups"][0]["modifiers"][0]["id"], 200
        )
        self.assertEqual(body["pagination"]["current"], 1)

    def _tracked_menu_product(self):
        product = self.env["product.template"].search([
            ("blend_config_id", "=", self.config.id), ("blend_external_id", "=", "100"),
        ])
        product.with_context(blend_inbound_sync=True).write({
            "is_storable": True, "blend_schedule_json": {},
        })
        product.pos_categ_ids.with_context(blend_inbound_sync=True).write({"blend_schedule_json": {}})
        # Keep this stock lifecycle test independent of native demo products
        # whose database IDs may collide with the fixture's external ID 100.
        self.config.write({
            "limit_categories": True,
            "iface_available_categ_ids": [(6, 0, product.pos_categ_ids.ids)],
        })
        self.config.blend_event_url = "https://blend.example.test/menu-events"
        return product

    def test_stock_webhook_then_http_menu_returns_false_and_restock_returns_true(self):
        product = self._tracked_menu_product()
        location = self.config.picking_type_id.default_location_src_id
        quant = self.env["stock.quant"]
        for change, expected in ((1, True), (-1, False), (-2, False), (3, True)):
            with self.subTest(change=change):
                quant._update_available_quantity(product.product_variant_id, location, change)
                self.assertIs(BlendStockAvailabilityService(self.env).stock_available(product, self.config)[product.id], expected)
                logs = self.env["blend.integration.log"].search([
                    ("config_id", "=", self.config.id), ("stock_check", "!=", False),
                    ("state", "=", "pending"),
                ])
                with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as send:
                    for log in logs:
                        log._process_outbound()
                self.assertEqual(send.call_count, 0 if change == -2 else 1)
                if send.call_count:
                    self.assertEqual(send.call_args.kwargs["json"]["event"], "menu.updated")
                    self.assertEqual(send.call_args.kwargs["json"]["entity"], {"id": "100", "type": "product"})
                response = self.url_open(
                    "/api/menu", headers=self._api_headers(),
                    params={"db": self.env.cr.dbname, "filter['product_id']": "100"},
                )
                self.assertEqual(response.status_code, 200, response.text)
                row = response.json()["categories"][0]["products"][0]
                self.assertIs(row["stock"], expected)
                self.assertEqual(row["available_branches"], [{
                    "branch_id": self.config.blend_branch_id, "is_stock": expected,
                }])
                self.assertIs(row["available_branches"][0]["is_stock"], expected)

    def test_http_order_rejects_zero_and_negative_stock_even_before_webhook_delivery(self):
        product = self._tracked_menu_product()
        location = self.config.picking_type_id.default_location_src_id
        for delta in (0, -1):
            with self.subTest(delta=delta):
                if delta:
                    self.env["stock.quant"]._update_available_quantity(product.product_variant_id, location, delta)
                payload = self._create_order_payload()
                response = self.url_open(
                    "/api/orders", method="POST", json=payload, headers=self._api_headers(),
                    params={"db": self.env.cr.dbname},
                )
                self.assertEqual(response.status_code, 409, response.text)
                self.assertFalse(response.json()["success"])
                self.assertIn("unavailable", response.json()["message"])
                self.assertFalse(self.env["pos.order"].search([
                    ("blend_partner_reference", "=", payload["partner_reference"]),
                ]))

    def test_create_order_is_idempotent_and_update_order_works(self):
        payload = self._create_order_payload()
        first = self.url_open(
            "/api/orders",
            method="POST",
            json=payload,
            headers=self._api_headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(first.status_code, 200, first.text)
        first_body = first.json()
        self.assertEqual(
            set(first_body), {"success", "message", "order_id"}
        )
        self.assertIs(first_body["success"], True)

        duplicate = self.url_open(
            "/api/orders",
            method="POST",
            json=payload,
            headers=self._api_headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json()["order_id"], first_body["order_id"])
        orders = self.env["pos.order"].search(
            [
                ("config_id", "=", self.config.id),
                ("blend_partner_reference", "=", payload["partner_reference"]),
            ]
        )
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders.blend_order_type, "delivery")
        self.assertEqual(orders.blend_aggregator, "Jahez")
        self.assertEqual(
            orders.blend_aggregator_order_id, payload["aggregator_order_id"]
        )
        self.assertEqual(orders.blend_discount_amount, 10.0)

        update = self.url_open(
            "/api/order/update",
            method="PUT",
            json={
                "order_id": first_body["order_id"],
                "branch_id": self.config.blend_branch_id,
                "brand_id": self.config.blend_brand_id,
                "status": "ACCEPTED",
            },
            headers=self._api_headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(update.status_code, 200, update.text)
        self.assertEqual(update.json()["status"], "ACCEPTED")
        orders.invalidate_recordset()
        self.assertEqual(orders.blend_status, "accepted")

    def test_create_order_accepts_exact_blend_documented_request(self):
        payload = self._create_order_payload()

        response = self.url_open(
            "/api/orders",
            method="POST",
            json=payload,
            headers=self._api_headers(),
            params={"db": self.env.cr.dbname},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()["success"], True)
        order = self.env["pos.order"].search(
            [
                ("config_id", "=", self.config.id),
                ("blend_partner_reference", "=", payload["partner_reference"]),
            ],
            limit=1,
        )
        self.assertEqual(order.blend_payload, payload)
        self.assertEqual(order.blend_order_type, "delivery")
        self.assertEqual(order.blend_aggregator, "Jahez")
        self.assertEqual(
            order.blend_aggregator_order_id, payload["aggregator_order_id"]
        )
        self.assertEqual(order.blend_discount_amount, 10.0)
        self.assertFalse(order.lines.filtered("blend_is_discount_line"))
        self.assertEqual(sum(order.lines.mapped("price_subtotal")), 90.0)
        self.assertEqual(sum(order.lines.mapped("price_subtotal_incl")), 103.5)
        base_line = order.lines.filtered(lambda line: not line.parent_line_id)
        modifier_line = order.lines - base_line
        self.assertEqual(base_line.qty, 2)
        self.assertEqual(base_line.price_unit, 47.75)
        self.assertEqual(base_line.price_subtotal_incl, 95.5)
        self.assertEqual(modifier_line.qty, 4)
        self.assertEqual(modifier_line.price_unit, 2.0)
        self.assertEqual(modifier_line.price_subtotal_incl, 8.0)
        self.assertEqual(modifier_line.modifier_selection_qty, 2)

    def test_create_order_adds_separate_modifier_totals_blend_109_example(self):
        products = self.env["product.template"].search([
            ("blend_config_id", "=", self.config.id),
            ("blend_external_id", "in", ["100", "200"]),
        ])
        for product in products:
            product.with_context(blend_inbound_sync=True).write({
                "blend_external_id": "873" if product.blend_external_id == "100" else "1228",
            })
        self.env["product.template"].with_context(blend_inbound_sync=True).create([
            {
                "name": "Blend test product %s" % external_id,
                "type": "consu",
                "available_in_pos": True,
                "sale_ok": True,
                "company_id": self.config.company_id.id,
                "blend_config_id": self.config.id,
                "blend_external_id": external_id,
                "blend_is_available": True,
            }
            for external_id in ("914", "894")
        ])
        payload = self._create_order_payload()
        payload.update({
            "subtotal": "109.00", "tax": "0.00", "total": "109.00",
            "products": [
                {"id": "873", "quantity": 1, "total": 50, "modifiers": [
                    {"id": "1228", "quantity": 1, "total": "12.00", "note": None},
                ]},
                {"id": "914", "quantity": 1, "total": 24, "modifiers": []},
                {"id": "894", "quantity": 1, "total": 23, "modifiers": []},
            ],
        })
        response = self.url_open(
            "/api/orders", method="POST", json=payload, headers=self._api_headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        order = self.env["pos.order"].search([
            ("config_id", "=", self.config.id),
            ("blend_partner_reference", "=", payload["partner_reference"]),
        ])
        self.assertEqual(len(order), 1)
        self.assertEqual(order.amount_total, 109.0)
        self.assertEqual(order.amount_tax, 0.0)
        self.assertEqual(order.blend_discount_amount, 0.0)
        self.assertEqual(len(order.lines), 4)
        self.assertEqual(sum(order.lines.mapped("price_subtotal")), 109.0)
        self.assertEqual(sum(order.lines.mapped("price_subtotal_incl")), 109.0)
        totals = {
            line.product_id.product_tmpl_id.blend_external_id: line.price_subtotal_incl
            for line in order.lines
        }
        self.assertEqual(totals, {"873": 50.0, "1228": 12.0, "914": 24.0, "894": 23.0})
        parent = order.lines.filtered(lambda line: line.product_id.product_tmpl_id.blend_external_id == "873")
        modifier = order.lines.filtered(lambda line: line.product_id.product_tmpl_id.blend_external_id == "1228")
        self.assertEqual(modifier.parent_line_id, str(parent.id))

    def test_create_order_multiplies_modifiers_by_parent_quantity_hunger_payload(self):
        existing = self.env["pos.config"].search([
            ("blend_branch_id", "=", "1300024"),
            ("company_id", "=", self.config.company_id.id),
        ])
        for config in existing:
            config.blend_branch_id = "temporarily-displaced-%s" % uuid.uuid4().hex
        self.config.write({"blend_branch_id": "1300024", "blend_brand_id": "1300011"})
        menu = copy.deepcopy(sample_menu(self.config.blend_branch_id))
        menu["payload"]["categories"][0]["items"] = ["873", "868", "881"]
        item = menu["payload"]["items"][0]
        menu["payload"]["items"] = []
        for external_id in ("873", "868", "881"):
            new_item = copy.deepcopy(item)
            new_item["id"] = external_id
            menu["payload"]["items"].append(new_item)
        topping = menu["payload"]["toppings"][0]
        menu["payload"]["toppings"] = []
        for group_id, option_id, item_id in (("20", "1228", "873"), ("21", "1178", "868")):
            new_topping = copy.deepcopy(topping)
            new_topping["id"] = group_id
            new_topping["items"] = [item_id]
            new_topping["options"][0]["id"] = option_id
            menu["payload"]["toppings"].append(new_topping)
        BlendMenuService(self.env, self.config).sync_menu(menu)

        payload = {
            "tax": "29.22", "note": "", "total": "224.00", "status": "PREPARING",
            "brand_id": "1300011", "customer": {"name": "HUNGER", "phone": "555550016"},
            "products": [
                {"id": "873", "note": None, "total": 50, "quantity": 1,
                 "modifiers": [{"id": "1228", "note": None, "total": "12.00", "quantity": 1}]},
                {"id": "868", "note": None, "total": 126, "quantity": 2,
                 "modifiers": [{"id": "1178", "note": None, "total": "11.00", "quantity": 1}]},
                {"id": "881", "note": None, "total": 14, "quantity": 2, "modifiers": []},
            ],
            "subtotal": "194.78", "branch_id": "1300024", "aggregator": "hunger-station",
            "order_type": "delivery", "schedule_at": None,
            "partner_reference": "hunger-station: 2004448796",
            "aggregator_order_id": "2004448796",
        }
        response = self.url_open(
            "/api/orders", method="POST", json=payload, headers=self._api_headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()["success"], True)
        order = self.env["pos.order"].search([
            ("config_id", "=", self.config.id),
            ("blend_partner_reference", "=", payload["partner_reference"]),
        ])
        self.assertEqual(len(order), 1)
        self.assertEqual(order.blend_payload, payload)
        self.assertEqual(order.amount_total, 224.0)
        self.assertEqual(order.blend_discount_amount, 0.0)
        lines = {line.product_id.product_tmpl_id.blend_external_id: line for line in order.lines}
        self.assertEqual({key: line.price_subtotal_incl for key, line in lines.items()},
                         {"873": 50.0, "1228": 12.0, "868": 126.0, "1178": 22.0, "881": 14.0})
        self.assertEqual(lines["1178"].qty, 2)
        self.assertEqual(lines["1178"].price_unit, 11.0)
        self.assertEqual(lines["1178"].modifier_selection_qty, 1)

    def test_create_order_modifier_can_cost_more_than_base_product(self):
        payload = self._create_order_payload()
        payload.update({"subtotal": 13.0, "tax": 0.0, "total": 13.0})
        payload["products"][0]["total"] = 5.0
        response = self.url_open(
            "/api/orders", method="POST", json=payload, headers=self._api_headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        order = self.env["pos.order"].search([
            ("blend_partner_reference", "=", payload["partner_reference"]),
        ])
        self.assertEqual(sorted(order.lines.mapped("price_subtotal_incl")), [5.0, 8.0])
        self.assertEqual(sorted(order.lines.mapped("price_unit")), [2.0, 2.5])

    def test_create_order_parent_quantity_header_discount_and_mismatch(self):
        payload = self._create_order_payload()
        payload.update({"subtotal": 103.5, "tax": 0.0, "total": 93.5})
        response = self.url_open(
            "/api/orders", method="POST", json=payload, headers=self._api_headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        order = self.env["pos.order"].search([
            ("config_id", "=", self.config.id),
            ("blend_partner_reference", "=", payload["partner_reference"]),
        ])
        self.assertEqual(order.amount_total, 93.5)
        self.assertEqual(order.blend_discount_amount, 10.0)
        self.assertEqual(order.lines.filtered("blend_is_discount_line").price_subtotal_incl, -10.0)
        modifier = order.lines.filtered(lambda line: line.parent_line_id)
        self.assertEqual(modifier.qty, 4)
        self.assertEqual(modifier.price_subtotal_incl, 8.0)

        invalid = self._create_order_payload()
        invalid.update({"subtotal": 100.0, "tax": 0.0, "total": 92.0})
        rejected = self.url_open(
            "/api/orders", method="POST", json=invalid, headers=self._api_headers(),
        )
        self.assertEqual(rejected.status_code, 400, rejected.text)
        self.assertIn("multiplied by parent quantity", rejected.json()["message"])
        self.assertFalse(self.env["pos.order"].search([
            ("blend_partner_reference", "=", invalid["partner_reference"]),
        ]))

    def test_create_order_rejects_modifier_inclusive_product_total(self):
        payload = self._create_order_payload()
        payload["products"][0]["total"] = payload["total"]
        response = self.url_open(
            "/api/orders", method="POST", json=payload, headers=self._api_headers(),
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["message"],
            "The sum of products[].total and modifiers[].total multiplied by parent quantity "
            "must equal total, or subtotal + tax when a header discount is provided.")
        self.assertFalse(self.env["pos.order"].search([
            ("blend_partner_reference", "=", payload["partner_reference"]),
        ]))
        log = self.env["blend.integration.log"].search([
            ("config_id", "=", self.config.id),
            ("event", "=", "pos.order.create"),
            ("external_reference", "=", payload["partner_reference"]),
        ])
        self.assertEqual(log.state, "failed")

    def test_create_order_without_modifiers_uses_product_total(self):
        product = self.env["product.template"].search([
            ("blend_config_id", "=", self.config.id),
            ("blend_external_id", "=", "100"),
        ])
        product.with_context(blend_inbound_sync=True).write({"modifier_grp_ids": [(5, 0, 0)]})
        payload = self._create_order_payload()
        payload["products"][0].update({"total": 103.5, "modifiers": []})
        response = self.url_open(
            "/api/orders", method="POST", json=payload, headers=self._api_headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        order = self.env["pos.order"].search([
            ("blend_partner_reference", "=", payload["partner_reference"]),
        ])
        self.assertEqual(len(order.lines), 1)
        self.assertEqual(order.lines.price_subtotal_incl, 103.5)
        self.assertEqual(order.lines.price_subtotal, 90.0)

    def test_create_order_derives_zero_discount_and_rejects_invalid_totals(self):
        payload = self._create_order_payload()
        payload.update({"subtotal": 100.0, "tax": 15.0, "total": 115.0})
        payload["products"][0]["total"] = 107.0
        response = self.url_open(
            "/api/orders",
            method="POST",
            json=payload,
            headers=self._api_headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 200, response.text)
        order = self.env["pos.order"].search(
            [("blend_partner_reference", "=", payload["partner_reference"])],
            limit=1,
        )
        self.assertEqual(order.blend_discount_amount, 0.0)

        invalid = self._create_order_payload()
        invalid.update({"subtotal": 100.0, "tax": 15.0, "total": 116.0})
        invalid["products"][0]["total"] = 108.0
        response = self.url_open(
            "/api/orders",
            method="POST",
            json=invalid,
            headers=self._api_headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["message"],
            "Invalid totals: total cannot exceed subtotal + tax.",
        )

        excessive = self._create_order_payload()
        excessive.update({"subtotal": 10.0, "tax": 20.0, "total": 5.0})
        excessive["products"][0]["total"] = 5.0
        response = self.url_open(
            "/api/orders",
            method="POST",
            json=excessive,
            headers=self._api_headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["message"],
            "Invalid totals: the implied discount cannot exceed subtotal.",
        )

    def test_create_order_requires_routing_fields_and_customer_is_optional(self):
        for field_name in ("order_type", "aggregator", "aggregator_order_id"):
            payload = self._create_order_payload()
            payload.pop(field_name)
            response = self.url_open(
                "/api/orders",
                method="POST",
                json=payload,
                headers=self._api_headers(),
                params={"db": self.env.cr.dbname},
            )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(
                response.json()["message"], "%s is required." % field_name
            )

        payload = self._create_order_payload()
        payload.pop("customer")
        response = self.url_open(
            "/api/orders",
            method="POST",
            json=payload,
            headers=self._api_headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()["success"], True)

    def test_create_order_rejects_unknown_or_unavailable_product(self):
        unknown_payload = self._create_order_payload()
        unknown_payload["products"][0]["id"] = "unknown-product"
        unknown = self.url_open(
            "/api/orders",
            method="POST",
            json=unknown_payload,
            headers=self._api_headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(unknown.status_code, 404, unknown.text)

        unavailable_payload = self._create_order_payload()
        product = self.env["product.template"].search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", "100"),
            ],
            limit=1,
        )
        product.with_context(blend_inbound_sync=True).blend_is_available = False
        unavailable = self.url_open(
            "/api/orders",
            method="POST",
            json=unavailable_payload,
            headers=self._api_headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(unavailable.status_code, 409, unavailable.text)

    def test_update_order_returns_404_for_unknown_order(self):
        response = self.url_open(
            "/api/order/update",
            method="PUT",
            json={
                "order_id": "unknown-order",
                "branch_id": self.config.blend_branch_id,
                "brand_id": self.config.blend_brand_id,
                "status": "ACCEPTED",
            },
            headers=self._api_headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_webhook_rejects_invalid_json(self):
        response = self.url_open(
            "/api/blend/webhook",
            data=b"{not-json",
            headers=self._headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["message"], "Invalid JSON body.")
        self.assertTrue(response.json()["trace_id"])

    def test_webhook_returns_not_found_for_unknown_branch(self):
        payload = {
            "event": "branch_availability",
            "event_time": "2026-09-01T13:00:00Z",
            "branch_id": "unknown-branch",
            "payload": {"branch_id": "unknown-branch", "is_available": False},
        }
        response = self.url_open(
            "/api/blend/webhook",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["status"], "failed")
        self.assertTrue(response.json()["trace_id"])

    def test_webhook_happy_path_uses_single_endpoint(self):
        payload = {
            "event": "branch_availability",
            "event_time": "2026-09-01T13:00:00Z",
            "branch_id": self.config.blend_branch_id,
            "payload": {
                "branch_id": self.config.blend_branch_id,
                "is_available": False,
            },
        }
        response = self.url_open(
            "/api/blend/webhook",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(response.json()["event"], "branch_availability")
        self.assertTrue(response.json()["trace_id"])
