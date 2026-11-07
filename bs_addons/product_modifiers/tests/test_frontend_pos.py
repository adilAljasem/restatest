from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestProductModifiersPosUi(TestPointOfSaleHttpCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.ui_modifier = cls.env["product.template"].create({
            "name": "UI Modifier Cheese",
            "type": "consu",
            "available_in_pos": True,
            "list_price": 2.5,
            "taxes_id": False,
        })
        cls.ui_modifier_group = cls.env["modifiers.groups"].create({
            "name": "UI Modifier Group",
            "modifier_ids": [(6, 0, cls.ui_modifier.ids)],
        })
        cls.ui_parent = cls.env["product.template"].create({
            "name": "UI Modifier Pizza",
            "type": "consu",
            "available_in_pos": True,
            "list_price": 10,
            "taxes_id": False,
            "is_modifier": True,
            "modifier_grp_ids": [(6, 0, cls.ui_modifier_group.ids)],
        })
        cls.ui_parent.get_modifier()
        cls.ui_parent.modifier_ids.write({
            "apply_selector": True,
            "min_selector": 1,
            "max_selector": 2,
        })

    def test_pos_modifier_flow(self):
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("product_modifiers_pos_flow", login="pos_user")
