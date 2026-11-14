from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestKitchenBilingualProductNames(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("ar_001")

        cls.attribute = cls.env["product.attribute"].create({"name": "Size"})
        cls.attribute.with_context(lang="ar_001").write({"name": "الحجم"})
        cls.attribute_value = cls.env["product.attribute.value"].create({
            "name": "Large",
            "attribute_id": cls.attribute.id,
        })
        cls.attribute_value.with_context(lang="ar_001").write({"name": "كبير"})
        cls.template = cls.env["product.template"].create({
            "name": "Burger",
            "available_in_pos": True,
            "attribute_line_ids": [
                (
                    0,
                    0,
                    {
                        "attribute_id": cls.attribute.id,
                        "value_ids": [(6, 0, cls.attribute_value.ids)],
                    },
                )
            ],
        })
        cls.template.with_context(lang="ar_001").write({"name": "برجر"})
        cls.product = cls.template.product_variant_ids[:1]
        cls.ptav = cls.template.attribute_line_ids.product_template_value_ids[:1]

    def test_product_and_attribute_translations(self):
        self.assertEqual(self.product.kitchen_name_en, "Burger")
        self.assertEqual(self.product.kitchen_name_ar, "برجر")
        self.assertEqual(self.attribute.kitchen_name_en, "Size")
        self.assertEqual(self.attribute.kitchen_name_ar, "الحجم")
        self.assertEqual(self.ptav.kitchen_name_en, "Large")
        self.assertEqual(self.ptav.kitchen_name_ar, "كبير")

    def test_fields_are_loaded_for_pos(self):
        config = self.env["pos.config"].create({"name": "Bilingual Kitchen POS"})
        for model_name in (
            "product.product",
            "product.attribute",
            "product.template.attribute.value",
        ):
            model = self.env[model_name]
            self.assertIn("kitchen_name_ar", model._load_pos_data_fields(config))
            self.assertIn("kitchen_name_en", model._load_pos_data_fields(config))

