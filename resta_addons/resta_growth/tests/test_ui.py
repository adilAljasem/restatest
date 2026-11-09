from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install", "resta_growth")
class TestGrowthUI(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        profile = cls.env["resta.growth.profile"]._get()
        profile.sudo().welcome_state = "done"
        cls.owner = new_test_user(cls.env, login="ui_owner", password="ui_owner_pw", name="مالك",
                                  groups="base.group_user,resta_growth.group_resta_owner,point_of_sale.group_pos_manager")
        cls.cashier = new_test_user(cls.env, login="ui_cashier", password="ui_cashier_pw", name="كاشير",
                                    groups="base.group_user,point_of_sale.group_pos_user")

    def test_owner_sees_badge(self):
        self.start_tour("/odoo/growth", "resta_growth_test_owner_badge", login="ui_owner")

    def test_cashier_does_not_see_badge(self):
        self.start_tour("/odoo", "resta_growth_test_no_badge", login="ui_cashier")

    def test_welcome_tour_runs(self):
        self.start_tour("/odoo/growth", "resta_growth_welcome", login="ui_owner")
