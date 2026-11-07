from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install", "bs_delivery_dashboard")
class TestDeliveryDashboard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dashboard = cls.env["bs.delivery.dashboard"]
        cls.provider = cls.env["bs.delivery.provider"].create(
            {
                "name": "Dashboard Provider",
                "code": "dashboard_test_provider",
                "connector_kind": "aggregator",
            }
        )
        cls.connection = cls.env["bs.delivery.connection"].create(
            {
                "name": "Dashboard Connection",
                "provider_id": cls.provider.id,
            }
        )
        cls.pos = cls.env["pos.config"].create({"name": "Dashboard Branch"})
        cls.store = cls.env["bs.delivery.store.binding"].create(
            {
                "connection_id": cls.connection.id,
                "pos_config_id": cls.pos.id,
                "remote_store_id": "DASH-STORE",
            }
        )
        cls.viewer = new_test_user(
            cls.env,
            login="dashboard_viewer",
            groups="base.group_user,bs_pos_delivery_core.group_delivery_viewer",
        )
        cls.filters = {"provider_id": cls.provider.id, "date": "2026-09-09", "period": "today"}

    def _order(self, suffix, **values):
        return (
            self.env["bs.delivery.order"]
            ._with_delivery_internal_update()
            .create(
                {
                    "connection_id": self.connection.id,
                    "store_binding_id": self.store.id,
                    "external_order_id": "DASH-%s" % suffix,
                    "amount_total": 100,
                    "commission_amount": 20,
                    "net_amount": 80,
                    "received_at": "2026-09-09 10:00:00",
                    **values,
                }
            )
        )

    def _data(self, **filters):
        return self.dashboard.get_dashboard_data({**self.filters, **filters})

    def _action_records(self, kind, **kwargs):
        action = self.dashboard.get_dashboard_action(kind, self.filters, **kwargs)
        return self.env[action["res_model"]].search(action["domain"])

    def test_period_totals_rates_and_qualifying_average(self):
        self._order("accepted", state="accepted")
        self._order("received", amount_total=200, net_amount=160, commission_amount=40)
        self._order("rejected", state="rejected")
        self._order("cancelled", state="cancelled")
        self._order("old", received_at="2026-09-08 10:00:00")
        data = self._data()
        self.assertEqual(data["metrics"]["orders"], 4)
        self.assertEqual(data["metrics"]["gmv"], 300)
        self.assertEqual(data["metrics"]["average_order_value"], 150)
        self.assertEqual(data["metrics"]["rejection_pct"], 25)
        self.assertEqual(data["metrics"]["cancellation_pct"], 25)
        self.assertEqual(len(self._action_records("orders")), 4)
        self.assertEqual(len(self._action_records("gmv")), 2)
        self.assertEqual(len(self._action_records("state", state="rejected")), 1)

    def test_empty_values_are_not_measured_zeros(self):
        data = self._data()
        for name in (
            "average_order_value",
            "rejection_pct",
            "cancellation_pct",
            "acceptance_minutes",
            "preparation_minutes",
        ):
            self.assertIsNone(data["metrics"][name])
        self.assertFalse(data["kpis"]["mom_comparable"])
        self.assertEqual(data["kpis"]["mom_growth_pct"], 0)

    def test_durations_exclude_missing_and_invalid_timestamps(self):
        self._order(
            "timed",
            accepted_at="2026-09-09 10:04:00",
            preparing_at="2026-09-09 10:05:00",
            ready_at="2026-09-09 10:17:00",
        )
        self._order("zero", accepted_at="2026-09-09 10:00:00")
        self._order("untimed")
        self._order(
            "invalid",
            accepted_at="2026-09-09 09:59:00",
            preparing_at="2026-09-09 10:00:00",
            ready_at="2026-09-09 09:59:00",
        )
        data = self._data()["metrics"]
        self.assertEqual(data["acceptance_minutes"], 2)
        self.assertEqual(data["acceptance_samples"], 2)
        self.assertEqual(data["preparation_minutes"], 12)
        self.assertEqual(data["preparation_samples"], 1)

    def test_presets_and_custom_range(self):
        self._order("yesterday", received_at="2026-09-08 10:00:00")
        self._order("today")
        self.assertEqual(self._data(period="yesterday")["metrics"]["orders"], 1)
        self.assertEqual(self._data(period="last7")["metrics"]["orders"], 2)
        self.assertEqual(self._data(period="month")["metrics"]["orders"], 2)
        self.assertEqual(
            self._data(period="custom", date_from="2026-09-08", date_to="2026-09-09")["metrics"][
                "orders"
            ],
            2,
        )
        for invalid in (
            {"period": "unknown"},
            {"period": "custom"},
            {"period": "custom", "date_from": "2026-09-10", "date_to": "2026-09-01"},
            {"period": "custom", "date_from": "2020-01-01", "date_to": "2026-09-01"},
            {"date": "bad-date"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                self._data(**invalid)

    def test_branch_and_environment_scope(self):
        self._order("scoped", state="delivered")
        other_pos = self.env["pos.config"].create({"name": "Other Dashboard POS"})
        self.assertEqual(self._data(pos_config_id=self.pos.id)["metrics"]["orders"], 1)
        self.assertEqual(self._data(pos_config_id=other_pos.id)["metrics"]["orders"], 0)
        self.assertEqual(self._data(environment="production")["metrics"]["orders"], 0)
        self.assertEqual(self._data(environment="test")["platforms"][0]["orders"], 1)
        self.assertEqual(self._data(pos_config_id=other_pos.id)["live"]["connections"], 0)

    def test_current_workload_and_late_drilldown_include_picked_up(self):
        old = fields.Datetime.now() - timedelta(days=2)
        order = self._order("picked-up", state="picked_up", received_at=old)
        data = self._data(period="custom", date_from="2000-01-01", date_to="2000-01-01")
        self.assertEqual(data["metrics"]["orders"], 0)
        self.assertEqual(data["live"]["active_orders"], 1)
        self.assertEqual(data["kpis"]["late_orders"], 1)
        self.assertEqual(self._action_records("late"), order)
        serialized = self.dashboard._serialize_order(order, self.env.company)
        self.assertTrue(serialized["is_late"])
        self.assertEqual(serialized["provider_id"], self.provider.id)

    def test_no_connections_is_not_healthy_api(self):
        self.connection.active = False
        data = self._data()
        self.assertEqual(data["live"]["connections"], 0)
        self.assertEqual(data["live"]["configured"], 0)
        self.assertNotIn("api_healthy", data)

    def test_viewer_cannot_request_administration_shortcuts(self):
        dashboard = self.dashboard.with_user(self.viewer)
        data = dashboard.get_dashboard_data(self.filters)
        self.assertFalse(data["permissions"]["integration"])
        self.assertEqual(data["queue_health"], {})
        for kind in ("outbox", "webhook", "connections", "catalog"):
            with self.subTest(kind=kind), self.assertRaises(AccessError):
                dashboard.get_dashboard_action(kind, self.filters)

    def test_invalid_scope_and_action_are_rejected(self):
        for values, error in (
            ({"company_ids": [987654321]}, AccessError),
            ({"provider_id": "invalid"}, ValidationError),
            ({"provider_id": 987654321}, AccessError),
            ({"environment": "staging"}, ValidationError),
        ):
            with self.subTest(values=values), self.assertRaises(error):
                self._data(**values)
        with self.assertRaises(ValidationError):
            self.dashboard.get_dashboard_action("delete", self.filters)

    def test_non_delivery_user_is_denied_all_dashboard_rpcs(self):
        user = new_test_user(self.env, login="dashboard_non_delivery", groups="base.group_user")
        dashboard = self.dashboard.with_user(user)
        for method, args in (
            ("get_dashboard_data", []),
            ("get_dashboard_options", []),
            ("get_dashboard_action", ["orders"]),
        ):
            with self.subTest(method=method), self.assertRaises(AccessError):
                getattr(dashboard, method)(*args)

    def test_timing_aggregate_respects_record_rules(self):
        self._order("visible", accepted_at="2026-09-09 10:02:00")
        hidden = self._order("hidden", accepted_at="2026-09-09 12:00:00")
        self.env["ir.rule"].create(
            {
                "name": "Dashboard restricted order",
                "model_id": self.env["ir.model"]._get_id("bs.delivery.order"),
                "domain_force": "[('id', '!=', %s)]" % hidden.id,
            }
        )
        data = self.dashboard.with_user(self.viewer).get_dashboard_data(self.filters)
        self.assertEqual(data["metrics"]["orders"], 1)
        self.assertEqual(data["metrics"]["acceptance_minutes"], 2)

    def test_local_midnight_matches_daily_bucket_and_drilldown(self):
        self.env.company.partner_id.tz = "Asia/Riyadh"
        self.env.company.business_day_start_hour = 0.0
        self._order("local-today", received_at="2026-09-08 21:30:00")
        data = self._data()
        self.assertEqual(data["metrics"]["gmv"], 100)
        self.assertEqual(data["kpis"]["today_gmv"], 100)
        self.assertEqual(len(self._action_records("orders")), 1)

    def test_business_day_attribution_follows_pos_business_day(self):
        company = self.env.company
        company.partner_id.tz = "Asia/Riyadh"
        company.business_day_start_hour = 6.0
        # 02:00 Riyadh on the 10th belongs to the business day of the 9th.
        late_night = self._order("late-night", received_at="2026-09-09 23:00:00")
        # 06:30 Riyadh on the 10th opens the business day of the 10th.
        morning = self._order("morning", received_at="2026-09-10 03:30:00")
        self.assertEqual(str(late_night.business_date), "2026-09-09")
        self.assertEqual(str(morning.business_date), "2026-09-10")
        # Same rule as POS orders (pos_business_day).
        pos_rule = self.env["pos.order"].new({"company_id": company.id})
        self.assertEqual(
            pos_rule._get_business_date(late_night.received_at), late_night.business_date
        )

        day_9 = self._data()
        self.assertEqual(day_9["metrics"]["orders"], 1)
        self.assertEqual(day_9["kpis"]["today_gmv"], 100)
        self.assertEqual(day_9["business_day"]["label"], "06:00")
        self.assertEqual(self._action_records("orders"), late_night)
        self.assertEqual(self._data(date="2026-09-10")["metrics"]["orders"], 1)
        start, end = company._bs_business_day_bounds("2026-09-09")
        self.assertEqual(fields.Datetime.to_string(start), "2026-09-09 03:00:00")
        self.assertEqual(fields.Datetime.to_string(end), "2026-09-10 03:00:00")

        # Changing the start hour re-attributes existing Connect orders.
        company.business_day_start_hour = 0.0
        self.assertEqual(str(late_night.business_date), "2026-09-10")
        self.assertEqual(self._data()["metrics"]["orders"], 0)

    def test_platform_sales_use_ordering_platform_not_middleware(self):
        self._order("hunger-1", external_source="HUNGER", state="delivered")
        self._order("hunger-2", external_source="HungerStation", amount_total=50)
        self._order("jahez", external_source="Jahez", amount_total=30)
        self._order("jahez-rejected", external_source="JAHEZ", state="rejected")
        self._order("direct", amount_total=20)
        data = self._data()
        by_key = {row["key"]: row for row in data["platforms"]}
        self.assertEqual(set(by_key), {"HUNGERSTATION", "JAHEZ", "DASHBOARDTESTPROVIDER"})
        self.assertEqual(by_key["HUNGERSTATION"]["name"], "HungerStation")
        self.assertEqual(by_key["HUNGERSTATION"]["orders"], 2)
        self.assertEqual(by_key["HUNGERSTATION"]["gmv"], 150)
        self.assertEqual(by_key["HUNGERSTATION"]["via"], ["Dashboard Provider"])
        self.assertFalse(by_key["HUNGERSTATION"]["provider_id"])
        # Rejected orders are not sales: platform totals reconcile with GMV.
        self.assertEqual(by_key["JAHEZ"]["orders"], 1)
        self.assertEqual(by_key["DASHBOARDTESTPROVIDER"]["name"], "Dashboard Provider")
        self.assertEqual(by_key["DASHBOARDTESTPROVIDER"]["provider_id"], self.provider.id)
        self.assertEqual(sum(row["gmv"] for row in data["platforms"]), data["metrics"]["gmv"])
        self.assertAlmostEqual(sum(row["share_pct"] for row in data["platforms"]), 100)
        self.assertEqual(data["platforms"][0]["key"], "HUNGERSTATION")

        hunger = self._data(channel="hunger station")
        self.assertEqual(hunger["metrics"]["orders"], 2)
        self.assertEqual(self._data(channel="JAHEZ")["metrics"]["orders"], 2)
        self.assertEqual(self._data(channel="dashboard_test_provider")["metrics"]["orders"], 1)
        self.assertEqual(self._data(channel="KEETA")["metrics"]["orders"], 0)
        self.assertEqual(len(self._action_records("gmv", channel="HUNGERSTATION")), 2)
        options = self.dashboard.get_dashboard_options()
        self.assertIn("JAHEZ", [item["key"] for item in options["channels"]])
        recent = {item["channel"]: item for item in data["recent_orders"]}
        self.assertEqual(recent["JAHEZ"]["channel_name"], "Jahez")
        self.assertFalse(recent["JAHEZ"]["channel_is_provider"])
        with self.assertRaises(ValidationError):
            self._data(channel="--")

    def test_branch_sales_breakdown(self):
        other_pos = self.env["pos.config"].create({"name": "Second Dashboard Branch"})
        other_store = self.env["bs.delivery.store.binding"].create(
            {
                "connection_id": self.connection.id,
                "pos_config_id": other_pos.id,
                "remote_store_id": "DASH-STORE-2",
            }
        )
        self._order("branch-1", amount_total=100)
        self._order("branch-2", amount_total=300, store_binding_id=other_store.id)
        self._order("branch-2-cancelled", store_binding_id=other_store.id, state="cancelled")
        data = self._data()
        branches = {row["pos_config_id"]: row for row in data["branches"]}
        self.assertEqual(branches[other_pos.id]["orders"], 1)
        self.assertEqual(branches[other_pos.id]["gmv"], 300)
        self.assertEqual(branches[self.pos.id]["gmv"], 100)
        self.assertEqual(data["branches"][0]["pos_config_id"], other_pos.id)
        self.assertEqual(branches[other_pos.id]["share_pct"], 75)
        self.assertEqual(
            len(self._action_records("gmv", pos_config_id=other_pos.id)), 1
        )

    def test_filters_and_aggregates_do_not_write_or_call_provider(self):
        self._order("read-only")
        connection_type = type(self.connection)
        with patch.object(
            connection_type, "write", side_effect=AssertionError("Dashboard must be read-only")
        ):
            self._data()
            options = self.dashboard.get_dashboard_options()
        self.assertIn(self.pos.id, [branch["id"] for branch in options["branches"]])

    def test_platform_and_recent_drilldowns_match_selected_period(self):
        selected = self._order("selected", state="delivered")
        self._order("older", state="delivered", received_at="2026-09-01 10:00:00")
        data = self._data()
        self.assertEqual(data["platforms"][0]["orders"], 1)
        self.assertEqual([item["id"] for item in data["recent_orders"]], selected.ids)
        self.assertEqual(data["recent_orders"][0]["provider_id"], self.provider.id)
        self.assertFalse(data["recent_orders"][0]["is_late"])
        self.assertEqual(self._action_records("delivered", provider_id=self.provider.id), selected)

    def test_other_company_is_not_disclosed_by_filters(self):
        other = self.env["res.company"].create({"name": "Private dashboard company"})
        dashboard = self.dashboard.with_user(self.viewer).with_context(
            allowed_company_ids=[self.env.company.id]
        )
        with self.assertRaises(AccessError):
            dashboard.get_dashboard_data({"company_ids": other.ids})
        options = dashboard.get_dashboard_options()
        self.assertNotIn(other.id, [item["id"] for item in options["companies"]])

    def test_queue_counts_and_shortcuts_share_connection_scope(self):
        event = self.env["bs.delivery.webhook.event"].create(
            {
                "connection_id": self.connection.id,
                "idempotency_key": "dashboard-failure",
                "payload_digest": "dashboard-payload",
                "state": "failed",
            }
        )
        other_provider = self.env["bs.delivery.provider"].create(
            {"name": "Other queue", "code": "other_dashboard_queue"}
        )
        other_connection = self.env["bs.delivery.connection"].create(
            {"name": "Other queue", "provider_id": other_provider.id}
        )
        self.env["bs.delivery.webhook.event"].create(
            {
                "connection_id": other_connection.id,
                "idempotency_key": "other-failure",
                "payload_digest": "other-payload",
                "state": "failed",
            }
        )
        self.assertEqual(self._data()["queue_health"]["webhook_retry"], 1)
        self.assertEqual(self._action_records("webhook"), event)
        self.assertEqual(self._data(pos_config_id=self.pos.id)["queue_health"]["webhook_retry"], 1)


@tagged("post_install", "-at_install", "bs_dashboard_assets")
class TestDeliveryDashboardAssets(TransactionCase):
    def test_backend_light_and_dark_assets_compile(self):
        for name in ("web.assets_web", "web.assets_web_dark"):
            with self.subTest(bundle=name):
                bundle = self.env["ir.qweb"]._get_asset_bundle(name, js=False)
                attachment = bundle.css()
                self.assertFalse(bundle.css_errors)
                css = attachment.raw.decode()
                self.assertIn("o_bs_aggregator_dashboard", css)
                self.assertIn("--bs-delivery-panel-bg", css)
                # Odoo 19 overrides Bootstrap's prefix to an empty string.
                # Literal --bs-table-* variables silently fail to theme tables.
                self.assertIn("--table-bg:var(--bs-delivery-panel-bg)", css.replace(" ", ""))
                self.assertIn("var(--body-color)", css)
                self.assertNotIn("var(--bs-body-color)", css)
                self.assertNotIn("A css error occured", css)

    def test_dashboard_js_and_templates_are_in_backend_bundle(self):
        bundle = self.env["ir.qweb"]._get_asset_bundle("web.assets_web", css=False)
        content = bundle.js().raw.decode()
        self.assertIn("bs_pos_delivery_core.AggregatorDashboard", content)
        self.assertIn("@bs_pos_delivery_core/dashboard/dashboard_utils", content)
        self.assertIn("Needs attention now", content)
