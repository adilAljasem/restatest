from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestProductVisibility(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["pos.config"].create(
            {
                "name": "Product Visibility Test POS",
                "company_id": cls.env.company.id,
            }
        )
        cls.hidden_product = cls.env["product.product"].create(
            {
                "name": "Hidden Combo Choice",
                "available_in_pos": True,
                "self_order_available": True,
                "hide_from_pos_self_order": True,
            }
        )
        cls.visible_product = cls.env["product.product"].create(
            {
                "name": "Visible Product",
                "available_in_pos": True,
                "self_order_available": True,
            }
        )

    def test_field_is_loaded_and_clients_keep_hidden_product_data(self):
        ProductTemplate = self.env["product.template"]

        self.assertIn(
            "hide_from_pos_self_order",
            ProductTemplate._load_pos_data_fields(self.config),
        )
        pos_products = ProductTemplate.search(
            ProductTemplate._load_pos_data_domain({}, self.config)
        )
        self_order_products = ProductTemplate.search(
            ProductTemplate._load_pos_self_data_domain({}, self.config)
        )

        self.assertIn(self.hidden_product.product_tmpl_id, pos_products)
        self.assertIn(self.hidden_product.product_tmpl_id, self_order_products)
        self.assertIn(self.visible_product.product_tmpl_id, self_order_products)

        loaded = ProductTemplate._load_pos_self_data_read({}, self.config)
        hidden_data = next(
            product
            for product in loaded
            if product["id"] == self.hidden_product.product_tmpl_id.id
        )
        self.assertTrue(hidden_data["hide_from_pos_self_order"])

    def test_hidden_combo_choice_is_still_loaded_for_self_order(self):
        combo = self.env["product.combo"].create(
            {
                "name": "Hidden Choice Combo",
                "combo_item_ids": [
                    Command.create(
                        {
                            "product_id": self.hidden_product.id,
                            "extra_price": 0,
                        }
                    )
                ],
            }
        )
        combo_product = self.env["product.product"].create(
            {
                "name": "Visible Combo",
                "type": "combo",
                "available_in_pos": True,
                "self_order_available": True,
                "combo_ids": [Command.link(combo.id)],
            }
        )

        loaded = self.env["product.template"]._load_pos_self_data_read({}, self.config)
        loaded_ids = {product["id"] for product in loaded}

        self.assertIn(combo_product.product_tmpl_id.id, loaded_ids)
        self.assertIn(self.hidden_product.product_tmpl_id.id, loaded_ids)
