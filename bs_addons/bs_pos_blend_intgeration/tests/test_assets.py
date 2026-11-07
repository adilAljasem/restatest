from odoo.tests.common import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval


@tagged("post_install", "-at_install")
class TestBlendAssets(TransactionCase):
    def test_inbound_blend_statusbar_is_readonly_below_order_header(self):
        view = self.env.ref("bs_pos_blend_intgeration.view_pos_pos_form_blend")
        arch = view._get_combined_arch()
        bar = arch.xpath("//header/following-sibling::*[1][@name='blend_order_statusbar']")[0]
        self.assertEqual(bar.get("invisible"), "not blend_is_inbound")
        field = bar.xpath("./field[@name='blend_status']")[0]
        self.assertEqual(field.get("widget"), "statusbar")
        self.assertEqual(field.get("readonly"), "1")
        self.assertEqual(field.get("options"), "{'clickable': False}")
        self.assertEqual(bar.xpath("./label")[0].get("for"), field.get("id"))
        self.assertIn("fw-bold", bar.xpath("./label")[0].get("class").split())
        self.assertIn("pt-0", bar.get("class").split())
        self.assertFalse(arch.xpath("//group[@name='blend_order_decision']//field[@name='blend_status']"))
        self.assertTrue(arch.xpath("//header/field[@name='state'][@widget='statusbar']"))

    def test_blend_decision_delivery_badge_matches_queue_state(self):
        arch = self.env.ref("bs_pos_blend_intgeration.view_pos_pos_form_blend")._get_combined_arch()
        bar = arch.xpath("//div[@name='blend_order_statusbar']")[0]
        delivery = bar.xpath("./div[@name='blend_decision_delivery']")[0]
        self.assertEqual(delivery.get("role"), "status")
        self.assertEqual(delivery.get("invisible"), "not blend_decision_sync_state")
        self.assertTrue(bar.xpath("./field[@name='blend_decision_sync_state'][@invisible='1']"))
        self.assertFalse(arch.xpath("//group[@name='blend_order_decision']//field[@name='blend_decision_sync_state']"))
        expected = {
            "received": ("Queued for Blend", "warning"),
            "pending": ("Queued for Blend", "warning"),
            "processing": ("Sending to Blend", "info"),
            "retry": ("Awaiting retry to Blend", "warning"),
            "success": ("Sent to Blend", "success"),
            "failed": ("Delivery to Blend failed", "danger"),
            "duplicate": ("Duplicate notification", "secondary"),
            "superseded": ("Notification superseded", "secondary"),
        }
        self.assertEqual(set(expected), set(dict(self.env["blend.integration.log"]._fields["state"].selection)))
        for state in [False, *expected]:
            with self.subTest(state=state):
                values = {"blend_decision_sync_state": state}
                badges = [
                    badge for badge in delivery.xpath("./span[@invisible]")
                    if not safe_eval(badge.get("invisible"), values)
                ]
                if state:
                    self.assertEqual(len(badges), 1)
                    label, color = expected[state]
                    self.assertEqual(badges[0].text, label)
                    self.assertIn("text-bg-%s" % color, badges[0].get("class").split())
                else:
                    self.assertFalse(badges)
                    self.assertTrue(safe_eval(delivery.get("invisible"), values))

    def test_blend_security_token_action_occupies_a_full_row(self):
        view = self.env.ref("bs_pos_blend_intgeration.pos_config_view_form_blend")
        arch = view._get_combined_arch()
        security = arch.xpath("//group[@name='blend_security']")[0]
        self.assertEqual(security.get("col"), "2")
        actions = security.xpath("./div[@name='blend_login_token_actions']")[0]
        self.assertEqual(actions.get("colspan"), "2")
        self.assertEqual(actions.get("invisible"), "not blend_login_token_issued_at")
        self.assertEqual(len(actions.xpath("./button[@name='action_revoke_blend_login_token']")), 1)
        self.assertFalse(security.xpath("./button"))
        for field_name in ("blend_inbound_token", "blend_outbound_token"):
            token = security.xpath("./field[@name='%s']" % field_name)[0]
            self.assertEqual(token.get("password"), "True")

    def test_provisioned_blend_user_is_an_active_portal_service_account(self):
        user = self.env.ref("bs_pos_blend_intgeration.user_blend_service")
        self.assertEqual(user.name, "Blend User")
        self.assertEqual(user.login, "blend_user")
        self.assertTrue(user.active)
        self.assertTrue(user.share)
        self.assertTrue(user.has_group("base.group_portal"))
        self.assertFalse(user.has_group("base.group_user"))

    def test_enabling_blend_prefills_the_provisioned_service_user(self):
        config = self.env["pos.config"].new({"blend_enabled": True})
        config._onchange_blend_default_login_user()
        self.assertEqual(
            config.blend_login_user_id,
            self.env.ref("bs_pos_blend_intgeration.user_blend_service"),
        )

    def test_pos_assets_compile(self):
        assets = self.env["ir.qweb"]._get_asset_bundle("point_of_sale._assets_pos")
        self.assertTrue(assets.js())
        self.assertTrue(assets.css())

    def test_blend_fields_are_loaded_to_pos(self):
        config = self.env["pos.config"].create(
            {
                "name": "Blend POS Data Test",
                "blend_enabled": True,
                "blend_branch_id": "asset-test-branch",
                "blend_inbound_token": "asset-test-token",
                "payment_method_ids": [(6, 0, [])],
            }
        )
        fields_to_load = self.env["pos.config"]._load_pos_data_fields(config)
        if fields_to_load:
            self.assertTrue(
                {
                    "use_pricelist",
                    "blend_enabled",
                    "blend_branch_id",
                    "blend_available",
                    "blend_schedule_json",
                    "blend_timezone",
                }.issubset(fields_to_load)
            )
        self.assertTrue(config.blend_login_url.endswith("/api/login"))
        self.assertTrue(config.blend_webhook_url.endswith("/api/blend/webhook"))
        self.assertTrue(config.blend_menu_api_url.endswith("/api/menu"))
        self.assertTrue(config.blend_branches_api_url.endswith("/api/branches"))
        self.assertTrue(config.blend_orders_api_url.endswith("/api/orders"))
        self.assertTrue(
            config.blend_order_update_api_url.endswith("/api/order/update")
        )
        config_data = self.env["pos.config"]._load_pos_data_search_read({}, config)
        self.assertEqual(len(config_data), 1)
        self.assertIn("use_pricelist", config_data[0])
        self.assertIn("blend_enabled", config_data[0])
        self.assertIn("blend_branch_id", config_data[0])
        self.assertIn("blend_available", config_data[0])
        modifier_fields = self.env["product.modifiers"]._load_pos_data_fields(config)
        self.assertTrue(
            {
                "blend_group_external_id",
                "blend_is_required",
                "blend_min_selection",
                "blend_max_selection",
                "blend_max_per_quantity",
                "blend_group_available",
            }.issubset(modifier_fields)
        )
