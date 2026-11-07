from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMrpBomComponent(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gram = cls.env.ref("uom.product_uom_gram")
        unit = cls.env.ref("uom.product_uom_unit")
        cls.pineapple = cls.env["product.product"].create(
            {"name": "Pineapple", "uom_id": cls.gram.id}
        )
        cls.salt = cls.env["product.product"].create(
            {"name": "Salt", "uom_id": cls.gram.id}
        )
        finished = cls.env["product.product"].create(
            {"name": "Sponge", "uom_id": unit.id}
        )
        cls.bom = cls.env["mrp.bom"].create(
            {
                "product_tmpl_id": finished.product_tmpl_id.id,
                "product_id": finished.id,
                "product_qty": 1.0,
                "product_uom_id": unit.id,
                "bom_line_ids": [
                    Command.create(
                        {
                            "product_id": cls.pineapple.id,
                            "product_qty": 18.15,
                            "product_uom_id": cls.gram.id,
                        }
                    ),
                    Command.create(
                        {
                            "product_id": cls.salt.id,
                            "product_qty": 0.31,
                            "product_uom_id": cls.gram.id,
                        }
                    ),
                ],
            }
        )

    def test_used_in_shows_quantity_of_opened_product(self):
        action = self.pineapple.action_used_in_bom()
        self.assertIsInstance(action["context"], dict)
        self.assertEqual(action["context"]["default_company_id"], self.env.company.id)
        self.assertIn(self.bom.id, self.env["mrp.bom"].search(action["domain"]).ids)

        bom = self.bom.with_context(**action["context"])
        self.assertAlmostEqual(bom.selected_component_qty, 18.15)
        self.assertEqual(bom.selected_component_uom_id, self.gram)

        salt_context = self.salt.action_used_in_bom()["context"]
        salt_bom = self.bom.with_context(**salt_context)
        self.assertAlmostEqual(salt_bom.selected_component_qty, 0.31)
        self.assertEqual(salt_bom.selected_component_uom_id, self.gram)

    def test_template_used_in_shows_matching_component(self):
        action = self.pineapple.product_tmpl_id.action_used_in_bom()
        self.assertIsInstance(action["context"], dict)
        bom = self.bom.with_context(**action["context"])
        self.assertAlmostEqual(bom.selected_component_qty, 18.15)
        self.assertEqual(bom.selected_component_uom_id, self.gram)

    def test_repeated_component_lines_use_one_unit(self):
        kilogram = self.env.ref("uom.product_uom_kgm")
        self.env["mrp.bom.line"].create(
            {
                "bom_id": self.bom.id,
                "product_id": self.pineapple.id,
                "product_qty": 0.01,
                "product_uom_id": kilogram.id,
            }
        )
        action = self.pineapple.action_used_in_bom()
        bom = self.bom.with_context(**action["context"])
        self.assertAlmostEqual(bom.selected_component_qty, 28.15)
        self.assertEqual(bom.selected_component_uom_id, self.gram)
