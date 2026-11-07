"""Explicit platform/type mapping on the target connection and POS branch."""

from odoo import Command
from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from ..exceptions import DeliveryBusinessError


@tagged("post_install", "-at_install")
class TestDeliveryPaymentResolver(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref(
            "bs_pos_delivery_core.group_delivery_integration_manager",
        )
        cls.provider = cls.env["bs.delivery.provider"].create({
            "name": "Payment Resolver Provider", "code": "payment_resolver_provider",
        })
        cls.connection = cls.env["bs.delivery.connection"].create({
            "name": "Payment Resolver Connection", "provider_id": cls.provider.id,
        })
        cls.bank_method = cls.bank_pm1
        cash_journal = cls.env["account.journal"].create({
            "name": "Resolver Cash", "code": "RSCASH", "type": "cash",
            "company_id": cls.env.company.id,
        })
        cls.cash_method = cls.cash_pm1.copy({
            "name": "Resolver Cash", "journal_id": cash_journal.id,
        })
        cls.config = cls.env["pos.config"].create({
            "name": "Payment Resolver POS",
            "module_pos_restaurant": False,
            "payment_method_ids": [Command.set((cls.bank_method | cls.cash_method).ids)],
        })
        cls.store = cls.env["bs.delivery.store.binding"].create({
            "connection_id": cls.connection.id,
            "pos_config_id": cls.config.id,
            "remote_store_id": "PAYMENT-STORE",
        })
        cls.Mapping = cls.env["bs.delivery.pos.payment.mapping"]
        cls.Service = cls.env["bs.delivery.payment.service"]

    def _row(self, name, payment_type, method):
        return self.Mapping.create({
            "connection_id": self.connection.id,
            "pos_config_id": self.config.id,
            "aggregator_name": name,
            "payment_type": payment_type,
            "payment_method_id": method.id,
        })

    def _resolve(self, code, name="HungerStation", collector="aggregator"):
        return self.Service._resolve_payment_method(
            self.connection, self.store, external_code=code,
            external_source=name, collected_by=collector,
        )

    def _assert_error(self, code, name, error_code):
        with self.assertRaises(DeliveryBusinessError) as raised:
            self._resolve(code, name)
        self.assertEqual(raised.exception.code, error_code)

    def test_connection_selects_explicit_platform_and_payment_type(self):
        self.connection.write({"delivery_payment_mapping_ids": [
            Command.create({
                "pos_config_id": self.config.id,
                "aggregator_name": " HungerStation ",
                "payment_type": "credit_visa", "payment_method_id": self.bank_method.id,
            }),
            Command.create({
                "pos_config_id": self.config.id,
                "aggregator_name": "HungerStation",
                "payment_type": "cash", "payment_method_id": self.cash_method.id,
            }),
        ]})
        self.assertEqual(self._resolve("visa", "hungerstation"), self.bank_method)
        self.assertEqual(
            self._resolve("cash", "HUNGERSTATION", "restaurant"), self.cash_method,
        )

    def test_platform_names_share_a_method_and_no_method_is_created(self):
        self._row("Jahez", "credit_visa", self.bank_method)
        self._row("HungerStation", "credit_visa", self.bank_method)
        original_count = self.env["pos.payment.method"].search_count([])
        self.assertEqual(self._resolve("0", "Jahez"), self.bank_method)
        self.assertEqual(self._resolve("card", "HungerStation"), self.bank_method)
        self.assertEqual(self.env["pos.payment.method"].search_count([]), original_count)

    def test_collector_does_not_change_the_configured_method(self):
        self._row("Jahez", "cash", self.cash_method)
        for collector in ("restaurant", "aggregator", "unknown"):
            self.assertEqual(self._resolve("cash", "Jahez", collector), self.cash_method)

    def test_operator_can_resolve_without_payment_setup_write_access(self):
        self._row("Jahez", "credit_visa", self.bank_method)
        operator = new_test_user(
            self.env, login="payment_setup_operator",
            groups="bs_pos_delivery_core.group_delivery_operator",
            company_id=self.config.company_id.id,
        )
        service = self.Service.with_user(operator)
        self.assertEqual(service._resolve_payment_method(
            self.connection.with_user(operator), self.store.with_user(operator),
            external_code="visa", external_source="Jahez", collected_by="aggregator",
        ), self.bank_method)

    def test_no_other_platform_or_default_method_is_used(self):
        self._row("Jahez", "credit_visa", self.bank_method)
        self._assert_error("visa", "HungerStation", "payment_mapping_not_found")
        self._assert_error("cash", "Jahez", "payment_mapping_not_found")
        self._assert_error("mystery", "Jahez", "invalid_payment_code")

    def test_same_branch_is_scoped_to_its_connection(self):
        self.Mapping.create({
            "connection_id": self.connection.id,
            "pos_config_id": self.config.id,
            "aggregator_name": "Jahez",
            "payment_type": "cash",
            "payment_method_id": self.cash_method.id,
        })
        other = self.env["bs.delivery.connection"].create({
            "name": "Second Payment Connection", "provider_id": self.provider.id,
        })
        other_store = self.env["bs.delivery.store.binding"].create({
            "connection_id": other.id,
            "pos_config_id": self.config.id,
            "remote_store_id": "PAYMENT-STORE-SECOND",
        })
        with self.assertRaises(DeliveryBusinessError) as raised:
            self.Service._resolve_payment_method(
                other, other_store, external_code="cash",
                external_source="Jahez", collected_by="restaurant",
            )
        self.assertEqual(raised.exception.code, "payment_mapping_not_found")
        self.Mapping.create({
            "connection_id": other.id,
            "pos_config_id": self.config.id,
            "aggregator_name": "Jahez",
            "payment_type": "cash",
            "payment_method_id": self.bank_method.id,
        })
        self.assertEqual(self._resolve("cash", "Jahez"), self.cash_method)
        self.assertEqual(self.Service._resolve_payment_method(
            other, other_store, external_code="cash",
            external_source="Jahez", collected_by="restaurant",
        ), self.bank_method)

    def test_same_connection_can_map_each_branch_to_its_own_method(self):
        self._row("Jahez", "cash", self.cash_method)
        other_config = self.env["pos.config"].create({
            "name": "Second Resolver POS",
            "module_pos_restaurant": False,
            "payment_method_ids": [Command.set(self.bank_method.ids)],
        })
        other_store = self.env["bs.delivery.store.binding"].create({
            "connection_id": self.connection.id,
            "pos_config_id": other_config.id,
            "remote_store_id": "PAYMENT-STORE-SECOND-BRANCH",
        })
        self.Mapping.create({
            "connection_id": self.connection.id,
            "pos_config_id": other_config.id,
            "aggregator_name": "Jahez",
            "payment_type": "cash",
            "payment_method_id": self.bank_method.id,
        })
        self.assertEqual(self._resolve("cash", "Jahez"), self.cash_method)
        self.assertEqual(self.Service._resolve_payment_method(
            self.connection, other_store, external_code="cash",
            external_source="Jahez", collected_by="restaurant",
        ), self.bank_method)

    def test_wrong_store_and_missing_source_are_rejected(self):
        other = self.env["bs.delivery.connection"].create({
            "name": "Unrelated", "provider_id": self.provider.id,
        })
        with self.assertRaises(DeliveryBusinessError) as raised:
            self.Service._resolve_payment_method(
                other, self.store, external_code="visa",
                external_source="Jahez", collected_by="aggregator",
            )
        self.assertEqual(raised.exception.code, "invalid_store")
        self._assert_error("", "Jahez", "invalid_payment_code")
        self._assert_error("visa", "", "invalid_payment_source")
