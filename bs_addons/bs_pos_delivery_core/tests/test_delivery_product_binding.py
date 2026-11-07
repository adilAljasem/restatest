from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

from ..exceptions import DeliveryBusinessError


@tagged("post_install", "-at_install")
class TestDeliveryProductBinding(TransactionCase):
    def setUp(self):
        super().setUp()
        self.provider = self.env["bs.delivery.provider"].create(
            {"name": "Mapping Test", "code": "mapping_test"}
        )
        self.connection = self.env["bs.delivery.connection"].create(
            {
                "name": "Mapping Test",
                "provider_id": self.provider.id,
                "company_id": self.env.company.id,
            }
        )
        self.product = self.env["product.product"].create(
            {"name": "Mapped Delivery Product"}
        )

    def test_product_mapping_resolves_normalized_external_id(self):
        binding = self.env["bs.delivery.product.binding"].create(
            {
                "connection_id": self.connection.id,
                "product_id": self.product.id,
                "external_product_id": "  EXT-100  ",
            }
        )
        self.assertEqual(binding.external_product_id, "EXT-100")
        self.assertEqual(
            self.env["bs.delivery.product.binding"].resolve_product(
                self.connection,
                [False, "EXT-100"],
            ),
            self.product,
        )

    def test_product_mapping_keeps_bilingual_name_and_platform_price(self):
        binding = self.env["bs.delivery.product.binding"].create(
            {
                "connection_id": self.connection.id,
                "product_id": self.product.id,
                "external_product_id": "BILINGUAL-100",
                "external_name_en": "Platform Burger",
                "external_name_ar": "برجر المنصة",
                "platform_price": 42.5,
            }
        )

        self.assertEqual(binding.external_name, "Platform Burger")
        self.assertEqual(binding.external_name_en, "Platform Burger")
        self.assertEqual(binding.external_name_ar, "برجر المنصة")
        self.assertEqual(binding.platform_price, 42.5)
        self.assertEqual(binding.currency_id, self.env.company.currency_id)

    def test_product_mapping_rejects_negative_platform_price(self):
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            self.env["bs.delivery.product.binding"].create(
                {
                    "connection_id": self.connection.id,
                    "product_id": self.product.id,
                    "external_product_id": "NEGATIVE-PRICE",
                    "platform_price": -1.0,
                }
            )

    def test_product_mapping_is_connection_scoped(self):
        self.env["bs.delivery.product.binding"].create(
            {
                "connection_id": self.connection.id,
                "product_id": self.product.id,
                "external_product_id": "EXT-200",
            }
        )
        other = self.env["bs.delivery.connection"].create(
            {
                "name": "Other Mapping Test",
                "provider_id": self.provider.id,
                "company_id": self.env.company.id,
            }
        )
        self.assertFalse(
            self.env["bs.delivery.product.binding"].resolve_product(
                other,
                ["EXT-200"],
            )
        )

    def test_reverse_lookup_prefers_store_override(self):
        config = self.env["pos.config"].create(
            {"name": "Reverse Mapping POS", "module_pos_restaurant": False}
        )
        store = self.env["bs.delivery.store.binding"].create(
            {
                "connection_id": self.connection.id,
                "pos_config_id": config.id,
                "remote_store_id": "REVERSE-STORE",
            }
        )
        global_binding = self.env["bs.delivery.product.binding"].create(
            {
                "connection_id": self.connection.id,
                "product_id": self.product.id,
                "external_product_id": "GLOBAL-SKU",
            }
        )
        store_binding = self.env["bs.delivery.product.binding"].create(
            {
                "connection_id": self.connection.id,
                "store_binding_id": store.id,
                "product_id": self.product.id,
                "external_product_id": "STORE-SKU",
            }
        )

        model = self.env["bs.delivery.product.binding"]
        self.assertEqual(
            model.resolve_binding_for_product(
                self.connection, self.product, store_binding=store
            ),
            store_binding,
        )
        self.assertEqual(
            model.resolve_binding_for_product(self.connection, self.product),
            global_binding,
        )

    def test_order_product_accepts_odoo_id_only_when_explicit(self):
        self.product.write({"available_in_pos": True, "sale_ok": True})
        config = self.env["pos.config"].create(
            {"name": "Product Identity POS", "module_pos_restaurant": False}
        )
        store = self.env["bs.delivery.store.binding"].create(
            {
                "connection_id": self.connection.id,
                "pos_config_id": config.id,
                "remote_store_id": "IDENTITY-STORE",
            }
        )
        resolver = self.env["bs.delivery.product.binding"]
        self.assertEqual(
            resolver.resolve_order_product(
                self.connection, store, str(self.product.id), identity_kind="odoo_variant"
            ),
            self.product,
        )
        with self.assertRaises(DeliveryBusinessError):
            resolver.resolve_order_product(self.connection, store, str(self.product.id))

    def test_order_product_rejects_other_branch_category(self):
        allowed_category = self.env["pos.category"].create({"name": "Allowed"})
        other_category = self.env["pos.category"].create({"name": "Other"})
        self.product.write({
            "available_in_pos": True,
            "sale_ok": True,
            "pos_categ_ids": [(6, 0, other_category.ids)],
        })
        config = self.env["pos.config"].create({
            "name": "Limited Product POS",
            "module_pos_restaurant": False,
            "limit_categories": True,
            "iface_available_categ_ids": [(6, 0, allowed_category.ids)],
        })
        store = self.env["bs.delivery.store.binding"].create({
            "connection_id": self.connection.id,
            "pos_config_id": config.id,
            "remote_store_id": "LIMITED-STORE",
        })
        with self.assertRaises(DeliveryBusinessError):
            self.env["bs.delivery.product.binding"].resolve_order_product(
                self.connection, store, str(self.product.id), identity_kind="odoo_variant"
            )

    def test_order_product_resolves_template_reference_without_manual_binding(self):
        self.product.write({"available_in_pos": True, "sale_ok": True})
        self.product.product_tmpl_id.default_code = "CATALOG-ITEM-44"
        config = self.env["pos.config"].create(
            {"name": "Catalog Identity POS", "module_pos_restaurant": False}
        )
        store = self.env["bs.delivery.store.binding"].create({
            "connection_id": self.connection.id,
            "pos_config_id": config.id,
            "remote_store_id": "CATALOG-STORE",
        })
        resolver = self.env["bs.delivery.product.binding"]
        self.assertEqual(
            resolver.resolve_order_product(
                self.connection, store, "CATALOG-ITEM-44",
                identity_kind="template_field", template_field="default_code",
            ),
            self.product,
        )
        self.assertEqual(
            resolver.resolve_order_product(
                self.connection, store, str(self.product.product_tmpl_id.id),
                identity_kind="odoo_template",
            ),
            self.product,
        )

    def test_disabled_store_binding_blocks_global_product(self):
        self.product.write({"available_in_pos": True, "sale_ok": True})
        config = self.env["pos.config"].create({"name": "Blocked Mapping POS"})
        store = self.env["bs.delivery.store.binding"].create({
            "connection_id": self.connection.id,
            "pos_config_id": config.id,
            "remote_store_id": "BLOCKED-STORE",
        })
        Mapping = self.env["bs.delivery.product.binding"]
        Mapping.create({
            "connection_id": self.connection.id,
            "external_product_id": "BLOCKED-PRODUCT",
            "product_id": self.product.id,
        })
        Mapping.create({
            "connection_id": self.connection.id,
            "store_binding_id": store.id,
            "external_product_id": "BLOCKED-PRODUCT",
            "product_id": self.product.id,
            "active": False,
        })
        with self.assertRaises(DeliveryBusinessError) as raised:
            Mapping.resolve_order_product(self.connection, store, "BLOCKED-PRODUCT")
        self.assertEqual(raised.exception.code, "product_unavailable")

    def test_odoo_identity_cannot_resolve_another_company_product(self):
        other_company = self.env["res.company"].create({"name": "Other delivery company"})
        other_product = self.env["product.product"].sudo().create({
            "name": "Other company's item", "company_id": other_company.id,
            "available_in_pos": True,
        })
        config = self.env["pos.config"].create({"name": "Company scoped POS"})
        store = self.env["bs.delivery.store.binding"].create({
            "connection_id": self.connection.id,
            "pos_config_id": config.id,
            "remote_store_id": "COMPANY-STORE",
        })
        with self.assertRaises(DeliveryBusinessError) as raised:
            self.env["bs.delivery.product.binding"].sudo().resolve_order_product(
                self.connection, store, other_product.id, identity_kind="odoo_variant"
            )
        self.assertEqual(raised.exception.code, "product_unavailable")
