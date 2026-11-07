from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase


class TestCategoryInventoryLocationSync(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "Inventory Location Company B"})

        cls.location_a = cls.env["stock.location"].with_company(cls.company_a).create({
            "name": "Category Inventory A",
            "usage": "inventory",
            "company_id": cls.company_a.id,
        })
        cls.location_a_2 = cls.env["stock.location"].with_company(cls.company_a).create({
            "name": "Category Inventory A 2",
            "usage": "inventory",
            "company_id": cls.company_a.id,
        })
        cls.location_b = cls.env["stock.location"].with_company(cls.company_b).create({
            "name": "Category Inventory B",
            "usage": "inventory",
            "company_id": cls.company_b.id,
        })

        cls.category = cls.env["product.category"].create({"name": "Location Source"})
        cls.category.with_company(cls.company_a).write({
            "property_stock_inventory": cls.location_a.id,
        })
        cls.category.with_company(cls.company_b).write({
            "property_stock_inventory": cls.location_b.id,
        })

    def test_field_definition_and_domain(self):
        field = self.env["product.category"]._fields["property_stock_inventory"]
        self.assertEqual(field.string, "Inventory Location")
        self.assertEqual(field.comodel_name, "stock.location")
        self.assertTrue(field.company_dependent)
        self.assertTrue(field.check_company)
        self.assertEqual(
            self.env["product.category"].fields_get(["property_stock_inventory"])[
                "property_stock_inventory"
            ]["domain"],
            "[('company_id', 'in', [allowed_company_ids[0]] + [False])] + "
            "[('usage', '=', 'inventory'), '|', ('company_id', '=', False), "
            "('company_id', '=', allowed_company_ids[0])]",
        )

    def test_create_product_uses_category_location(self):
        product_a = self.env["product.template"].with_company(self.company_a).create({
            "name": "Company A Product",
            "categ_id": self.category.id,
            "company_id": self.company_a.id,
        })
        product_b = self.env["product.template"].with_company(self.company_b).create({
            "name": "Company B Product",
            "categ_id": self.category.id,
            "company_id": self.company_b.id,
        })

        self.assertEqual(product_a.with_company(self.company_a).property_stock_inventory, self.location_a)
        self.assertEqual(product_b.with_company(self.company_b).property_stock_inventory, self.location_b)

    def test_changing_product_category_replaces_and_clears_location(self):
        other_category = self.env["product.category"].create({"name": "Other Location Source"})
        other_category.with_company(self.company_a).write({
            "property_stock_inventory": self.location_a_2.id,
        })
        empty_category = self.env["product.category"].create({"name": "No Inventory Location"})
        product = self.env["product.template"].with_company(self.company_a).create({
            "name": "Recategorized Product",
            "categ_id": self.category.id,
            "company_id": self.company_a.id,
        })

        product.write({"categ_id": other_category.id})
        self.assertEqual(product.property_stock_inventory, self.location_a_2)

        product.write({"categ_id": empty_category.id})
        self.assertFalse(product.property_stock_inventory)

    def test_category_change_updates_active_and_archived_products(self):
        products = self.env["product.template"].with_company(self.company_a).create([
            {
                "name": "Active Product",
                "categ_id": self.category.id,
                "company_id": self.company_a.id,
            },
            {
                "name": "Archived Product",
                "categ_id": self.category.id,
                "company_id": self.company_a.id,
                "active": False,
            },
        ])

        self.category.with_company(self.company_a).write({
            "property_stock_inventory": self.location_a_2.id,
        })

        self.assertEqual(
            products.with_context(active_test=False).mapped("property_stock_inventory"),
            self.location_a_2,
        )

        self.category.with_company(self.company_a).write({"property_stock_inventory": False})
        self.assertFalse(any(products.with_context(active_test=False).mapped("property_stock_inventory")))

    def test_category_values_and_sync_are_company_isolated(self):
        shared_product = self.env["product.template"].with_company(self.company_a).create({
            "name": "Shared Product",
            "categ_id": self.category.id,
            "company_id": False,
        })
        product_b = self.env["product.template"].with_company(self.company_b).create({
            "name": "Company B Product",
            "categ_id": self.category.id,
            "company_id": self.company_b.id,
        })

        self.category.with_company(self.company_a).write({
            "property_stock_inventory": self.location_a_2.id,
        })

        self.assertEqual(shared_product.with_company(self.company_a).property_stock_inventory, self.location_a_2)
        self.assertEqual(product_b.with_company(self.company_b).property_stock_inventory, self.location_b)

        self.category.with_company(self.company_b).write({
            "property_stock_inventory": self.location_b.id,
        })
        self.assertEqual(shared_product.with_company(self.company_b).property_stock_inventory, self.location_b)
        self.assertEqual(shared_product.with_company(self.company_a).property_stock_inventory, self.location_a_2)

    def test_reject_location_from_another_company(self):
        with self.assertRaises(ValidationError):
            self.category.with_company(self.company_a).write({
                "property_stock_inventory": self.location_b.id,
            })
