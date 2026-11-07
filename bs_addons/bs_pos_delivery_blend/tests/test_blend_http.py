import uuid

from odoo import Command
from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestBlendWebhookHttp(HttpCase):
    def test_login_route_contract(self):
        rule = next(
            rule for rule in self.env["ir.http"].routing_map().iter_rules()
            if str(rule) == "/api/login"
        )
        routing = rule.endpoint.routing
        self.assertEqual(set(routing["methods"]), {"POST"})
        self.assertEqual(routing["auth"], "public")
        self.assertEqual(routing["type"], "http")
        self.assertFalse(routing["csrf"])
        self.assertFalse(routing["save_session"])

    def test_menu_route_contract(self):
        rule = next(
            rule for rule in self.env["ir.http"].routing_map().iter_rules()
            if str(rule) == "/api/menu"
        )
        routing = rule.endpoint.routing
        self.assertEqual(set(routing["methods"]), {"GET"})
        self.assertEqual(routing["auth"], "public")
        self.assertEqual(routing["type"], "http")
        self.assertFalse(routing["csrf"])
        self.assertFalse(routing["save_session"])

    def test_branches_route_contract(self):
        rule = next(
            rule for rule in self.env["ir.http"].routing_map().iter_rules()
            if str(rule) == "/api/branches"
        )
        routing = rule.endpoint.routing
        self.assertEqual(set(routing["methods"]), {"GET"})
        self.assertEqual(routing["auth"], "public")
        self.assertEqual(routing["type"], "http")
        self.assertFalse(routing["csrf"])
        self.assertFalse(routing["save_session"])

    def test_orders_route_contract(self):
        rule = next(
            rule for rule in self.env["ir.http"].routing_map().iter_rules()
            if str(rule) == "/api/orders"
        )
        routing = rule.endpoint.routing
        self.assertEqual(set(routing["methods"]), {"POST"})
        self.assertEqual(routing["auth"], "public")
        self.assertEqual(routing["type"], "http")
        self.assertFalse(routing["csrf"])
        self.assertFalse(routing["save_session"])

    def test_order_update_route_contract(self):
        rule = next(
            rule for rule in self.env["ir.http"].routing_map().iter_rules()
            if str(rule) == "/api/order/update"
        )
        routing = rule.endpoint.routing
        self.assertEqual(set(routing["methods"]), {"PUT"})
        self.assertEqual(routing["auth"], "public")

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


@tagged("post_install", "-at_install")
class TestBlendLiveHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.execution_user = new_test_user(
            cls.env,
            login="blend-http-execution-user",
            password="Execution#&=K)P^",
            groups="base.group_user,point_of_sale.group_pos_user,bs_pos_delivery_core.group_delivery_operator",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.token = "live-preshared-token-123"
        cls.user_blend = cls.env.ref("bs_pos_delivery_blend.user_blend_service")
        cls.user_blend.sudo().write({"password": "BlendSecure123!"})

        pm_bank = cls.env["pos.payment.method"].search([
            ("journal_id.type", "=", "bank"),
            ("company_id", "=", cls.company.id),
        ], limit=1)
        suffix = uuid.uuid4().hex[:6]
        journal_cash = cls.env["account.journal"].create({
            "name": "Live Cash %s" % suffix,
            "type": "cash",
            "code": "C%s" % suffix[:4].upper(),
            "company_id": cls.company.id,
        })
        pm_cash = cls.env["pos.payment.method"].create({
            "name": "Live Cash PM %s" % suffix,
            "journal_id": journal_cash.id,
            "company_id": cls.company.id,
        })

        cls.pos_config = cls.env["pos.config"].create({
            "name": "Blend Live HTTP POS",
            "blend_enabled": True,
            "blend_branch_id": "BLEND-LIVE-BRANCH",
            "blend_brand_id": "BLEND-LIVE-BRAND",
            "blend_login_user_id": cls.user_blend.id,
            "blend_inbound_token": cls.token,
            "payment_method_ids": [Command.set([pm_bank.id, pm_cash.id])],
        })
        cls.pos_session = cls.env["pos.session"].create({"config_id": cls.pos_config.id})

        cls.provider = cls.env.ref("bs_pos_delivery_core.provider_blend")
        cls.connection = cls.env["bs.delivery.connection"].create({
            "name": "Blend Live HTTP Connection",
            "provider_id": cls.provider.id,
            "company_id": cls.company.id,
            "state": "connected",
            "webhook_secret": cls.token,
            "pos_config_ids": [Command.set(cls.pos_config.ids)],
        })
        cls.connection.menu_pricelist_id = cls.env["product.pricelist"].create({
            "name": "Blend Live HTTP Menu Prices",
            "company_id": cls.company.id,
            "currency_id": cls.company.currency_id.id,
        })
        cls.store = cls.connection._blend_store_for_config(cls.pos_config)
        cls.connection.write({
            "delivery_payment_mapping_ids": [
                Command.create({
                    "pos_config_id": cls.pos_config.id,
                    "aggregator_name": "Jahez",
                    "payment_type": "credit_visa",
                    "payment_method_id": pm_bank.id,
                }),
                Command.create({
                    "pos_config_id": cls.pos_config.id,
                    "aggregator_name": "Jahez",
                    "payment_type": "cash",
                    "payment_method_id": pm_cash.id,
                }),
            ]
        })

        cls.product = cls.env["product.product"].with_context(blend_inbound_sync=True).create({
            "name": "Blend Live Burger",
            "available_in_pos": True,
            "list_price": 25.0,
            "taxes_id": [Command.clear()],
            "blend_external_id": "99101",
            "image_1920": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
        })
        cls.env["bs.delivery.product.binding"].create({
            "connection_id": cls.connection.id,
            "store_binding_id": cls.store.id,
            "external_product_id": "99101",
            "product_id": cls.product.id,
        })
        cls.env.flush_all()

    def _headers(self, token=None):
        headers = {
            "Content-Type": "application/json",
            "X-Odoo-Database": self.env.cr.dbname,
        }
        if token:
            headers["Authorization"] = "Bearer %s" % token
        return headers

    def test_rejected_order_has_synchronization_log(self):
        payload = {
            "branch_id": "BLEND-LIVE-BRANCH",
            "brand_id": "BLEND-LIVE-BRAND",
            "aggregator_order_id": "BLEND-AUDIT-REJECTED",
            "unsupported_field": True,
        }
        response = self.url_open(
            "/api/orders", method="POST", json=payload,
            headers=self._headers(self.token),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 422, response.text)
        logs = self.env["bs.delivery.sync.log"].search([
            ("connection_id", "=", self.connection.id),
            ("operation", "=", "blend:create_order"),
            ("external_id", "=", payload["aggregator_order_id"]),
        ])
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs.http_status, 422)
        self.assertIn("unsupported_field", logs.request_payload)
        self.assertIn("supported JSON fields", logs.response_payload)

    def test_live_login_success_and_rotation(self):
        # 1. Invalid credentials
        bad_res = self.url_open(
            "/api/login",
            method="POST",
            json={"email": "blend_user", "password": "wrong_password"},
            params={"db": self.env.cr.dbname},
            headers=self._headers(),
        )
        self.assertEqual(bad_res.status_code, 401, bad_res.text)
        self.assertEqual(bad_res.json(), {"status": "Credentials do not match"})

        # 2. Valid credentials
        res = self.url_open(
            "/api/login",
            method="POST",
            json={"email": "blend_user", "password": "BlendSecure123!"},
            params={"db": self.env.cr.dbname},
            headers=self._headers(),
        )
        self.assertEqual(res.status_code, 200, res.text)
        token1 = res.json().get("access_token")
        self.assertTrue(token1)

        # 3. Second login issues new token
        res2 = self.url_open(
            "/api/login",
            method="POST",
            json={"email": "blend_user", "password": "BlendSecure123!"},
            params={"db": self.env.cr.dbname},
            headers=self._headers(),
        )
        self.assertEqual(res2.status_code, 200)
        token2 = res2.json().get("access_token")
        self.assertNotEqual(token1, token2)

    def test_live_get_branches_endpoint(self):
        token = "live-preshared-token-123"
        # 1. Unauthorized
        unauth = self.url_open(
            "/api/branches",
            headers=self._headers(),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(unauth.status_code, 401)

        # 2. Authorized
        res = self.url_open(
            "/api/branches",
            headers=self._headers(token),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertIn("branches", body)
        self.assertEqual(len(body["branches"]), 1)
        branch = body["branches"][0]
        self.assertEqual(branch["id"], "BLEND-LIVE-BRANCH")
        self.assertEqual(branch["brand_id"], "BLEND-LIVE-BRAND")

    def test_live_connection_basic_login(self):
        self.connection.write({
            "auth_scheme": "basic",
            "username": "live-connection-login",
            "password": "Connection#&=K)P^",
            "blend_execution_user_id": self.execution_user.id,
            "webhook_secret": False,
        })
        self._check_connection_login_and_actor("live-connection-login", "Connection#&=K)P^")

    def test_live_connection_odoo_user_login(self):
        self.connection.write({
            "blend_login_method": "odoo_user",
            "blend_login_user_id": self.execution_user.id,
        })
        self._check_connection_login_and_actor(self.execution_user.login, "Execution#&=K)P^")

    def test_live_odoo_selector_rejects_unselected_pos_user(self):
        self.connection.write({
            "blend_login_method": "odoo_user",
            "blend_login_user_id": self.env.user.id,
        })
        self.pos_config.blend_login_user_id = self.execution_user
        response = self.url_open(
            "/api/login", method="POST",
            json={"email": self.execution_user.login, "password": "Execution#&=K)P^"},
            headers=self._headers(), params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 401, response.text)

    def test_live_odoo_login_reports_missing_delivery_permission(self):
        self.execution_user.group_ids -= self.env.ref("bs_pos_delivery_core.group_delivery_operator")
        self.connection.write({
            "blend_login_method": "odoo_user", "blend_login_user_id": self.execution_user.id,
        })
        for password, expected_status in (("wrong-password", 401), ("Execution#&=K)P^", 403)):
            response = self.url_open(
                "/api/login", method="POST",
                json={"email": self.execution_user.login, "password": password},
                headers=self._headers(), params={"db": self.env.cr.dbname},
            )
            self.assertEqual(response.status_code, expected_status, response.text)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("Delivery Operator", response.json()["status"])
        self.assertNotIn("access_token", response.json())
        self.assertFalse(self.connection.blend_login_token_hash)

    def _check_connection_login_and_actor(self, login, correct_password):
        self.pos_config.write({"blend_login_user_id": False, "blend_inbound_token": False})
        for password, expected_status in (
            ("wrong-password", 401),
            (correct_password, 200),
        ):
            res = self.url_open(
                "/api/login",
                method="POST",
                json={"email": login, "password": password},
                params={"db": self.env.cr.dbname},
                headers=self._headers(),
            )
            self.assertEqual(res.status_code, expected_status, res.text)
            self.assertEqual(res.headers["Cache-Control"], "no-store")
            if expected_status == 401:
                self.assertEqual(res.json(), {"status": "Credentials do not match"})
        token = res.json()["access_token"]
        for endpoint in ("/api/branches", "/api/menu"):
            response = self.url_open(
                endpoint, headers=self._headers(token), params={"db": self.env.cr.dbname},
            )
            self.assertEqual(response.status_code, 200, response.text)
        payload = {
            "branch_id": "BLEND-LIVE-BRANCH",
            "brand_id": "BLEND-LIVE-BRAND",
            "partner_reference": "Jahez: basic-login-order",
            "aggregator": "Jahez",
            "aggregator_order_id": "basic-login-order",
            "order_type": "delivery",
            "total": 25.0,
            "subtotal": 25.0,
            "tax": 0.0,
            "status": "PENDING",
            "payment_type": "cash",
            "customer": {"name": "Basic Login Customer", "phone": "0559988776"},
            "products": [{"id": "99101", "quantity": 1, "total": 25.0, "modifiers": []}],
        }
        response = self.url_open(
            "/api/orders", method="POST", json=payload,
            headers=self._headers(token), params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["success"])
        order_id = response.json()["order_id"]
        response = self.url_open(
            "/api/order/update", method="PUT",
            json={"branch_id": "BLEND-LIVE-BRANCH", "brand_id": "BLEND-LIVE-BRAND",
                  "order_id": order_id, "status": "ACCEPTED"},
            headers=self._headers(token), params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.env.invalidate_all()
        order = self.env["bs.delivery.order"].sudo().search([("blend_api_order_id", "=", order_id)])
        self.assertEqual(order.create_uid, self.execution_user)
        self.assertEqual(order.webhook_event_id.create_uid, self.execution_user)
        self.assertEqual(order.pos_order_id.create_uid, self.execution_user)
        accepted = order.transition_ids.filtered(lambda transition: transition.to_state == "accepted")
        self.assertEqual(accepted.actor_id, self.execution_user)

    def test_live_get_menu_endpoint(self):
        token = "live-preshared-token-123"
        res = self.url_open(
            "/api/menu",
            headers=self._headers(token),
            params={"db": self.env.cr.dbname, "page": 1, "per": 10},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertIn("categories", body)
        self.assertIn("pagination", body)
        self.assertEqual(body["pagination"]["current"], 1)

    def test_live_order_creation_curbside_and_lifecycle(self):
        token = "live-preshared-token-123"
        payload = {
            "branch_id": "BLEND-LIVE-BRANCH",
            "brand_id": "BLEND-LIVE-BRAND",
            "partner_reference": "Jahez: 771122",
            "aggregator": "Jahez",
            "aggregator_order_id": "771122",
            "order_type": "delivery",
            "note": "Extra sauce",
            "total": 25.0,
            "subtotal": 25.0,
            "tax": 0.0,
            "status": "PENDING",
            "customer": {
                "name": "Live Customer",
                "phone": "0559988776",
                "car": {
                    "color": "Midnight Blue",
                    "plate_number": "9999",
                    "plate_letters": "KSA",
                    "car_brand": "Genesis",
                },
            },
            "products": [
                {
                    "id": "99101",
                    "quantity": 1,
                    "total": 25.0,
                    "modifiers": [],
                }
            ],
        }

        # 1. Create order
        res = self.url_open(
            "/api/orders",
            method="POST",
            json=payload,
            headers=self._headers(token),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(res.status_code, 200, res.text)
        res_data = res.json()
        self.assertTrue(res_data["success"])
        order_id = res_data["order_id"]
        self.assertTrue(order_id)

        # 2. Idempotent replay
        replay_res = self.url_open(
            "/api/orders",
            method="POST",
            json=payload,
            headers=self._headers(token),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(replay_res.status_code, 200)
        self.assertEqual(replay_res.json()["order_id"], order_id)

        # 3. Update status to ACCEPTED
        accept_res = self.url_open(
            "/api/order/update",
            method="PUT",
            json={
                "order_id": order_id,
                "branch_id": "BLEND-LIVE-BRANCH",
                "brand_id": "BLEND-LIVE-BRAND",
                "status": "ACCEPTED",
            },
            headers=self._headers(token),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(accept_res.status_code, 200, accept_res.text)
        self.assertEqual(accept_res.json()["status"], "ACCEPTED")

        # 4. Verify POS order created with curbside info
        self.env.invalidate_all()
        core_order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "Jahez: 771122"),
        ])
        self.assertEqual(core_order.state, "accepted")
        pos_order = core_order.pos_order_id
        self.assertTrue(pos_order)
        self.assertEqual(pos_order.blend_car_brand, "Genesis")
        self.assertEqual(pos_order.blend_car_color, "Midnight Blue")
        self.assertEqual(pos_order.blend_car_plate_number, "9999")
        self.assertEqual(pos_order.blend_car_plate_letters, "KSA")
        self.assertEqual(pos_order.blend_aggregator, "Jahez")

        # 5. Update status to PREPARING
        prep_res = self.url_open(
            "/api/order/update",
            method="PUT",
            json={
                "order_id": order_id,
                "branch_id": "BLEND-LIVE-BRANCH",
                "brand_id": "BLEND-LIVE-BRAND",
                "status": "PREPARING",
            },
            headers=self._headers(token),
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(prep_res.status_code, 200, prep_res.text)
        self.assertEqual(prep_res.json()["status"], "PREPARING")
        core_order.invalidate_recordset()
        self.assertEqual(core_order.state, "preparing")

    def test_live_product_image_endpoint(self):
        res = self.url_open(
            "/api/blend/products/%s/image" % self.product.product_tmpl_id.id,
            headers={"X-Odoo-Database": self.env.cr.dbname},
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("image", res.headers.get("Content-Type", ""))

