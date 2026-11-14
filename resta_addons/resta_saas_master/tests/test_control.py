import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTenantControl(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env["resta.plan"].create({
            "name": "النمو", "price_monthly": 499, "price_yearly": 4990, "max_branches": 2,
            "growth_plan": "paid_full", "max_users": 10, "max_pos": 3, "app_names": "hr_attendance, pos_loyalty"})
        cls.tenant = cls.env["resta.tenant"].create({
            "name": "مطعم الاختبار", "business_type": "dinein", "owner_name": "المالك",
            "owner_email": "owner@example.com", "owner_phone": "0500000000"})
        cls.tenant.write({"state": "trial"})

    def test_trial_entitlements(self):
        ent = self.tenant._entitlements()
        self.assertEqual(ent["growth_plan"], "trial")
        self.assertEqual(ent["max_users"], 0)

    def test_subscription_gives_plan_and_queues_apps(self):
        today = fields.Date.today()
        self.env["resta.subscription"].create({"tenant_id": self.tenant.id, "plan_id": self.plan.id, "branches": 2,
                                               "date_start": today, "date_end": today + timedelta(days=365)})
        self.tenant.write({"state": "active", "plan_id": self.plan.id})
        ent = self.tenant._entitlements()
        self.assertEqual((ent["growth_plan"], ent["max_users"], ent["max_pos"]), ("paid_full", 10, 3))
        command = self.tenant.command_ids.filtered(lambda c: c.kind == "install_modules")
        self.assertEqual(json.loads(command.payload)["modules"], ["hr_attendance", "pos_loyalty"])

    def test_orders_travel_with_the_sync(self):
        wizard = self.env["resta.tenant.command.wizard"].create({
            "tenant_id": self.tenant.id, "kind": "unlock_features", "features": "accounting", "sync_now": False})
        wizard.action_send()
        command = self.tenant.command_ids
        self.assertEqual(command.state, "pending")
        payload = self.tenant._sync_response(0)
        self.assertEqual(payload["commands"], [{"id": command.id, "kind": "unlock_features",
                                                "payload": {"keys": ["accounting"]}}])
        self.assertEqual(command.state, "sent")
        self.assertIn("entitlements", payload)
        self.tenant._record_acks([{"id": command.id, "state": "done", "result": "فُتح"}])
        self.assertEqual(command.state, "done")
        # another restaurant cannot report on this order
        other = self.env["resta.tenant"].create({"name": "آخر", "business_type": "qsr", "owner_name": "x",
                                                 "owner_email": "x@example.com"})
        command.write({"state": "sent"})
        other._record_acks([{"id": command.id, "state": "failed"}])
        self.assertEqual(command.state, "sent")

    def test_override_per_restaurant(self):
        self.tenant.write({"growth_plan_override": "standard", "max_users_override": 4})
        ent = self.tenant._entitlements()
        self.assertEqual((ent["growth_plan"], ent["max_users"]), ("standard", 4))

    def test_sync_now_signs_the_request(self):
        self.tenant.write({"db_name": "r_test_tenant"})
        with patch("odoo.addons.resta_saas_master.models.resta_control.requests.post") as post:
            post.return_value.raise_for_status.return_value = None
            self.tenant.action_sync_now()
        url = post.call_args.args[0]
        body = post.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/resta/tenant/ping"))
        self.assertEqual(body["account"], self.tenant.account_number)
        self.assertTrue(body["sig"])


@tagged("post_install", "-at_install")
class TestUnifiedSignInPage(HttpCase):

    def test_sign_in_page(self):
        html = self.url_open("/resta/login?account=100245").text
        self.assertIn('name="account"', html)
        self.assertIn('value="100245"', html)
        self.assertIn('name="login"', html)
        self.assertIn("اسم المستخدم", html)
        self.assertIn("rs_auth", html)
        self.assertNotIn("www.odoo.com", html)

    def test_short_link(self):
        resp = self.url_open("/login?account=100245", allow_redirects=False)
        self.assertIn(resp.status_code, (301, 302, 303))
        self.assertTrue(resp.headers["Location"].endswith("/resta/login?account=100245"))
