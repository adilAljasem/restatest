from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import GrowthCase


@tagged("post_install", "-at_install", "resta_growth")
class TestScore(GrowthCase):

    def test_forming_before_14_days(self):
        self.make_days(10)
        res = self.engine._compute_score(self.profile, self.today)
        self.assertTrue(res["forming"])

    def test_score_bounds_and_bands(self):
        self.make_days(90, inventory_count=lambda d: d.weekday() == 6,
                       inventory_coverage=lambda d: 90.0 if d.weekday() == 6 else 0,
                       inventory_adjust_value=lambda d: 150.0 if d.weekday() == 6 else 0)
        res = self.engine._compute_score(self.profile, self.today)
        self.assertFalse(res["forming"])
        self.assertTrue(0 <= res["score"] <= 1000)
        self.assertGreaterEqual(res["score"], 800, res)
        self.assertEqual(res["band"], "جاهز لفرع ثانٍ")
        for comp in res["components"].values():
            self.assertTrue(comp["reason"])
            self.assertTrue(comp["tip"])

    def test_weights_are_settings(self):
        self.make_days(30)
        self.env["ir.config_parameter"].sudo().set_param("resta_growth.w_invoices", 0)
        res = self.engine._compute_score(self.profile, self.today)
        self.assertEqual(res["components"]["invoices"]["points"], 0)

    def test_snapshot_is_immutable(self):
        self.make_days(20)
        snap = self.env["resta.growth.score.snapshot"].browse(self.engine._snapshot(self.profile, self.today).id)
        with self.assertRaises(UserError):
            snap.write({"score": 1000})
        with self.assertRaises(UserError):
            snap.unlink()
        # the engine itself may refresh today's snapshot (support «تحديث الآن»)
        self.engine._snapshot(self.profile, self.today)

    def test_snapshot_read_only_for_owner(self):
        self.make_days(20)
        snap = self.engine._snapshot(self.profile, self.today)
        with self.assertRaises(AccessError):
            snap.with_user(self.owner).write({"score": 1})

    def test_quality_index_range(self):
        self.make_days(40)
        q = self.engine._quality_index(self.profile, self.today)
        self.assertTrue(0 <= q <= 100)


@tagged("post_install", "-at_install", "resta_growth")
class TestLocks(GrowthCase):

    def setUp(self):
        super().setUp()
        self.stock_root = self.env.ref("stock.menu_stock_root")
        self.env["resta.growth.feature"].sudo().search([]).with_context(
            resta_growth_simulator=True)._relock_for_simulator()

    def visible(self, user):
        return self.env["ir.ui.menu"].with_user(user)._visible_menu_ids()

    def test_inventory_hidden_for_owner_visible_for_support(self):
        self.assertNotIn(self.stock_root.id, self.visible(self.owner))
        self.assertIn(self.stock_root.id, self.visible(self.support))

    def test_model_access_untouched(self):
        """Locking is UI only: the owner can still read stock moves (POS creates them)."""
        self.env["stock.move"].with_user(self.owner).search([], limit=1)

    def test_locked_action_listed(self):
        _menus, actions = self.env["resta.growth.feature"]._locked_ids()
        action = self.env.ref("stock.action_view_inventory_tree")
        self.assertIn((action._name, action.id), actions)

    def test_unlock_and_never_relock(self):
        self.inventory._unlock()
        self.assertIn(self.stock_root.id, self.visible(self.owner))
        with self.assertRaises(ValidationError):
            self.inventory.write({"unlocked": False})

    def test_paid_full_unlocks_everything(self):
        self.profile.sudo().plan = "paid_full"
        self.assertTrue(all(self.env["resta.growth.feature"].search([]).mapped("unlocked")))

    def test_owner_cannot_change_plan(self):
        with self.assertRaises(AccessError):
            self.profile.with_user(self.owner).write({"plan": "paid_full"})


@tagged("post_install", "-at_install", "resta_growth")
class TestJourney(GrowthCase):

    def setUp(self):
        super().setUp()
        self.env["resta.growth.feature"].sudo().search([]).with_context(
            resta_growth_simulator=True)._relock_for_simulator()
        states = self.profile.level_state_ids
        states.write({"state": "locked", "achieved_date": False, "ready_since": False})
        states.filtered(lambda s: s.level_id == self.level1).write({"state": "ready"})
        self.profile.sudo().current_level_id = self.level1

    def test_claim_unlocks_feature_and_starts_next_level(self):
        res = self.profile.with_user(self.owner).action_claim_reward()
        self.assertTrue(self.inventory.unlocked)
        self.assertFalse(self.accounting.unlocked)
        self.assertEqual(self.profile.current_level_id, self.level2)
        self.assertEqual(res["feature"]["key"], "inventory")
        self.assertIn("مبروك", res["message"])

    def test_cashier_cannot_claim(self):
        with self.assertRaises(AccessError):
            self.profile.with_user(self.cashier).action_claim_reward()

    def test_level_becomes_ready_when_all_conditions_met(self):
        self.profile.level_state_ids.filtered(lambda s: s.level_id == self.level1).state = "progress"
        Status = self.env["resta.growth.condition.status"]
        for cond in self.level1.condition_ids:
            Status._store(self.profile, cond, {"current_value": 1, "target": 1, "progress_pct": 100,
                                               "met": True, "details": {}})
        self.profile._refresh_level_state()
        state = self.profile.level_state_ids.filtered(lambda s: s.level_id == self.level1)
        self.assertEqual(state.state, "ready")

    def test_badge_owner_only(self):
        self.assertTrue(self.engine.with_user(self.owner).get_badge_data()["visible"])
        self.assertFalse(self.engine.with_user(self.cashier).get_badge_data()["visible"])
        manager = self.engine.with_user(self.owner).get_badge_data()
        self.assertTrue(manager["subscription"])

    def test_skip_needs_paid_full(self):
        with self.assertRaises(UserError):
            self.profile.with_user(self.owner).action_skip_level()

    def test_simulator_needs_flag(self):
        with self.assertRaises(UserError):
            self.env["resta.growth.simulator"].with_user(self.support).generate()
        with self.assertRaises(AccessError):
            self.env["resta.growth.simulator"].with_user(self.owner).generate()

    def test_simulator_generate_and_reset(self):
        self.env["ir.config_parameter"].sudo().set_param("resta_growth.simulator_enabled", "True")
        Sim = self.env["resta.growth.simulator"].with_user(self.support)
        Sim.generate("disciplined", days=60)
        self.assertTrue(self.profile.simulated)
        self.assertGreater(self.env["resta.growth.score.snapshot"].search_count([]), 30)
        Sim.set_level(3)
        self.assertTrue(self.accounting.unlocked)
        Sim.reset()
        self.assertFalse(self.inventory.unlocked)
        self.assertEqual(self.profile.current_level_id, self.level1)

    def test_owner_saves_business_settings(self):
        """The owner has no technical admin rights but can save the settings pages."""
        owner = self.env["res.users"].browse(self.env["res.users"]._resta_owner_group_ids() and self.owner.id)
        owner.write({"group_ids": [(6, 0, self.env["res.users"]._resta_owner_group_ids())]})
        self.assertFalse(owner.has_group("base.group_system"))
        settings = self.env["res.config.settings"].with_user(owner).create({})
        settings.execute()
        with self.assertRaises(AccessError):
            self.env["res.config.settings"].with_user(owner).create({"resta_growth_simulator_enabled": True})

    def test_owner_split_at_provisioning(self):
        admin = self.env.ref("base.user_admin")
        admin.write({"login": "owner@example.com", "name": "مالك جديد"})
        owner_id = self.env["res.users"]._resta_growth_setup_owner("cafe")
        owner = self.env["res.users"].browse(owner_id)
        self.assertEqual(owner.login, "owner@example.com")
        self.assertTrue(owner.has_group("resta_growth.group_resta_owner"))
        self.assertFalse(owner.has_group("base.group_system"))
        self.assertEqual(admin.login, "resta-support")
        self.assertTrue(admin.has_group("resta_growth.group_resta_support"))
        self.assertEqual(self.profile.restaurant_type_id.code, "cafe")
        self.assertEqual(self.profile.welcome_state, "new")
