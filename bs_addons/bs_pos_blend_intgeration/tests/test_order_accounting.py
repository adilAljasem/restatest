import uuid
from unittest.mock import patch

from psycopg2.errors import SerializationFailure, UniqueViolation

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import HttpCase, new_test_user
from odoo.addons.point_of_sale.tests.common import CommonPosTest

from ..services.pos_api import BlendPOSAPIService
from ..services.menu import BlendMenuService
from ..services import outbound
from ..services.exceptions import BlendAPIError
from .common import sample_menu, configure_blend_preset
from .test_outbound import FakeResponse


class BlendAccountingCommon(CommonPosTest):
    def setUp(self):
        super().setUp()
        self.env["res.lang"]._activate_lang("ar_001")
        self.config = self.pos_config_usd
        configure_blend_preset(self.config)
        self.config.write({
            "blend_enabled": True, "blend_brand_id": "accounting-brand",
            "blend_inbound_token": "accounting-test-only-token",
            "blend_branch_id": "accounting-branch", "blend_schema": "pos",
            "blend_status_url": "https://blend.example.test/status",
            "blend_finalize_on_accept": True, "blend_sync_images": False,
            "blend_invoice_partner_id": self.partner_mobt.id,
        })
        # Resta kitchen screen for the branch (every category)
        self.kitchen = self.env["resta.kds.display"].create({
            "name": "Blend kitchen (all)", "pos_config_ids": [Command.set(self.config.ids)]})
        self.mapping = self.env["blend.payment.mapping"].create({
            "pos_config_id": self.config.id, "aggregator": "Jahez",
            "payment_method_id": self.bank_payment_method.id,
        })
        BlendMenuService(self.env, self.config).sync_menu(sample_menu(self.config.blend_branch_id))
        self.item = self.env["product.template"].search([
            ("blend_config_id", "=", self.config.id), ("blend_external_id", "=", "100"),
        ])
        self.modifier = self.env["product.template"].search([
            ("blend_config_id", "=", self.config.id), ("blend_external_id", "=", "200"),
        ])
        (self.item | self.modifier).write({
            "taxes_id": [Command.clear()],
            "property_account_income_id": self.company_data["default_account_revenue"].id,
        })
        self.config.write({
            "limit_categories": True,
            "iface_available_categ_ids": [Command.set(self.item.pos_categ_ids.ids)],
        })
        self.pos_session = self.env["pos.session"].create({"config_id": self.config.id})
        self.pos_session.set_opening_control(0, "")
        self.service = BlendPOSAPIService(self.env, self.config)

    def _payload(self, total=62, tax=0, subtotal=None, base=50, modifier=12, quantity=1):
        reference = uuid.uuid4().hex
        return {
            "branch_id": self.config.blend_branch_id, "brand_id": self.config.blend_brand_id,
            "partner_reference": "jahez: " + reference, "aggregator_order_id": reference,
            "aggregator": " jahez ", "order_type": "delivery", "status": "PENDING",
            "total": total, "tax": tax, "subtotal": total - tax if subtotal is None else subtotal,
            "products": [{"id": "100", "quantity": quantity, "total": base,
                          "modifiers": [{"id": "200", "quantity": 1, "total": modifier}]}],
        }

    def _create(self, payload=None):
        result = self.service.create_order(payload or self._payload())
        self.assertTrue(result["success"])
        return self.env["pos.order"].search([("uuid", "=", result["order_id"])])

    def _logs(self, order):
        return self.env["blend.integration.log"].search([
            ("order_id", "=", order.id), ("direction", "=", "outbound"), ("event", "=", "order.updated"),
        ])

    def _assert_done(self, order, total=62, tax=0):
        self.assertEqual(order.blend_financial_state, "done", order.blend_financial_error)
        self.assertEqual(order.state, "done")
        self.assertEqual(order.blend_status, "accepted")
        self.assertEqual(len(order.payment_ids), 1 if total else 0)
        self.assertAlmostEqual(order.amount_paid, total, places=2)
        self.assertAlmostEqual(order.amount_total, total, places=2)
        self.assertAlmostEqual(order.amount_tax, tax, places=2)
        invoice = order.account_move
        self.assertEqual(len(invoice), 1)
        self.assertEqual(invoice.state, "posted")
        self.assertAlmostEqual(invoice.amount_total, total, places=2)
        self.assertAlmostEqual(invoice.amount_tax, tax, places=2)
        self.assertTrue(invoice.currency_id.is_zero(invoice.amount_residual))
        self.assertTrue(invoice.company_id.currency_id.is_zero(sum(invoice.line_ids.mapped("balance"))))
        for move in order.payment_ids.account_move_id:
            self.assertEqual(move.state, "posted")
            self.assertTrue(move.company_id.currency_id.is_zero(sum(move.line_ids.mapped("balance"))))
        self.assertEqual(order.blend_payment_method_id, self.mapping.payment_method_id)
        self.assertEqual(len(self._logs(order)), 1)
        self.assertEqual(self._logs(order).request_payload["status"], "ACCEPTED")
        self.assertFalse(order.blend_financial_error)

    def _assert_pending_failure(self, order):
        self.assertEqual(order.state, "draft")
        self.assertEqual(order.blend_status, "pending")
        self.assertEqual(order.blend_financial_state, "failed")
        self.assertTrue(order.blend_financial_error)
        self.assertFalse(order.payment_ids)
        self.assertFalse(order.account_move)
        self.assertFalse(self._logs(order))


@tagged("post_install", "-at_install", "blend_accounting")
class TestBlendAccountingHttp(BlendAccountingCommon, HttpCase):
    def _post_order(self, payload):
        response = self.url_open(
            "/api/orders", method="POST", json=payload,
            headers={"Authorization": "Bearer accounting-test-only-token", "X-Odoo-Database": self.env.cr.dbname},
            params={"db": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["success"])
        self.env.invalidate_all()
        return self.env["pos.order"].search([("uuid", "=", response.json()["order_id"])])

    def test_http_auto_accept_tax_invoice_payment_and_replay(self):
        self.config.blend_auto_accept = True
        (self.item | self.modifier).taxes_id = self.tax_fiften_excl
        payload = self._payload(total=103.5, tax=13.5, subtotal=100, base=95.5, modifier=4, quantity=2)
        order = self._post_order(payload)
        self._assert_done(order, 103.5, 13.5)
        payment, invoice = order.payment_ids, order.account_move
        self.assertEqual(self._post_order(payload), order)
        self.assertEqual(order.payment_ids, payment)
        self.assertEqual(order.account_move, invoice)
        self.assertEqual(len(self._logs(order)), 1)

    def test_http_missing_mapping_preserves_order_then_local_retry(self):
        self.config.blend_auto_accept = True
        self.mapping.active = False
        payload = self._payload()
        order = self._post_order(payload)
        self._assert_pending_failure(order)
        self.mapping.active = True
        self.assertEqual(self._post_order(payload), order)
        self._assert_pending_failure(order)
        order.action_blend_accept_order()
        self._assert_done(order)


@tagged("post_install", "-at_install", "blend_accounting")
class TestBlendOrderAccounting(BlendAccountingCommon):
    def test_manual_accept_native_payment_invoice_and_reconciliation(self):
        order = self._create()
        self.assertEqual(order.state, "draft")
        self.assertFalse(order.payment_ids)
        self.assertTrue(order.blend_taxes_prepared)
        with patch.object(outbound.requests, "post") as post:
            order.action_blend_accept_order()
            post.assert_not_called()
        self._assert_done(order)
        self.assertEqual(order.account_move.partner_id, self.partner_mobt)
        self.assertEqual(order.payment_ids.payment_method_id, self.bank_payment_method)
        self.assertEqual(order.payment_ids.account_move_id.state, "posted")

    def test_automatic_accept_has_same_financial_result(self):
        self.config.blend_auto_accept = True
        order = self._create()
        self._assert_done(order)
        self.assertEqual(order.blend_decision_source, "automatic")

    def test_scheduled_auto_accept_pays_and_invoices_once(self):
        self.config.blend_auto_accept = True
        payload = self._payload()
        payload.update({"status": "SCHEDULED", "schedule_at": "2026-10-01 18:30:00", "note": "Extra napkins"})
        order = self._create(payload)
        self._assert_done(order)
        self.assertEqual(order.blend_schedule_at, payload["schedule_at"])
        tickets = self.env["resta.kds.ticket"].search([("pos_order_id", "=", order.id)])
        self.assertEqual(len(tickets), 1)
        self.assertIn("Extra napkins", tickets.note)
        self.assertIn(payload["schedule_at"][:16], tickets.info)
        payment, invoice = order.payment_ids, order.account_move
        self.assertEqual(self._create(payload), order)
        order._blend_auto_accept()
        order.action_blend_mark_scheduled_prepared()
        self.assertEqual((order.payment_ids, order.account_move), (payment, invoice))
        self.assertEqual(len(self._logs(order)), 1)

    def test_auto_accept_progressed_order_accounts_and_prepares_once(self):
        self.config.blend_auto_accept = True
        self.kitchen.unlink()
        display = self.env["resta.kds.display"].create({
            "name": "Blend kitchen", "pos_config_ids": [Command.set(self.config.ids)],
            "category_ids": [Command.set(self.item.pos_categ_ids.ids)],
        })
        for status in ("ACCEPTED", "PREPARING", "ASSIGNED"):
            with self.subTest(status=status):
                payload = self._payload()
                payload["status"] = status
                with patch.object(outbound.requests, "post") as post:
                    order = self._create(payload)
                    post.assert_not_called()
                self.assertEqual(order.blend_financial_state, "done", order.blend_financial_error)
                self.assertEqual(order.blend_status, status.lower())
                self.assertEqual(order.blend_decision_source, "automatic")
                self.assertEqual(order.account_move.state, "posted")
                self.assertEqual(order.amount_paid, 62)
                self.assertEqual(order.account_move.amount_residual, 0)
                self.assertFalse(self._logs(order))  # Never send an older ACCEPTED status.
                tickets = self.env["resta.kds.ticket"].search([("pos_order_id", "=", order.id)])
                self.assertEqual(len(tickets), 1)
                self.assertEqual(tickets.display_id, display)
                self.assertTrue(tickets.line_ids)
                payment, invoice = order.payment_ids, order.account_move
                self.assertEqual(self._create(payload), order)
                order._blend_auto_accept()
                order.action_blend_accept_order()
                self.assertEqual((order.payment_ids, order.account_move), (payment, invoice))
                self.assertEqual(self.env["resta.kds.ticket"].search([("pos_order_id", "=", order.id)]), tickets)

    def test_advanced_delivery_status_accounts_without_kitchen_dispatch(self):
        self.config.blend_auto_accept = True
        for status in ("READY_TO_PICK_UP", "AT_PICK_UP", "ON_THE_WAY", "COMPLETED"):
            with self.subTest(status=status):
                payload = self._payload()
                payload["status"] = status
                order = self._create(payload)
                self.assertEqual(order.blend_status, status.lower())
                self.assertEqual(order.blend_financial_state, "done", order.blend_financial_error)
                self.assertEqual(order.state, "done")
                self.assertEqual(order.amount_paid, 62)
                self.assertEqual(order.account_move.state, "posted")
                self.assertFalse(self._logs(order))
                self.assertFalse(self.env["resta.kds.ticket"].search([("pos_order_id", "=", order.id)]))
                payment, invoice = order.payment_ids, order.account_move
                self.assertEqual(self._create(payload), order)
                self.assertEqual((order.payment_ids, order.account_move), (payment, invoice))

    def test_auto_accept_pending_order_sends_preparation(self):
        self.config.blend_auto_accept = True
        order = self._create()
        self._assert_done(order)
        self.assertEqual(self.env["resta.kds.ticket"].search_count([("pos_order_id", "=", order.id)]), 1)

    def test_progressed_order_respects_disabled_auto_accept(self):
        self.config.blend_auto_accept = False
        payload = self._payload()
        payload["status"] = "PREPARING"
        order = self._create(payload)
        self.assertEqual(order.state, "draft")
        self.assertFalse(order.payment_ids)
        self.assertFalse(order.account_move)
        self.assertFalse(self.env["resta.kds.ticket"].search([("pos_order_id", "=", order.id)]))

    def test_terminal_orders_are_not_paid_or_sent_to_preparation(self):
        self.config.blend_auto_accept = True
        for status in ("CANCELED", "REJECTED", "TIME_OUT"):
            with self.subTest(status=status):
                payload = self._payload()
                payload["status"] = status
                order = self._create(payload)
                self.assertEqual(order.state, "cancel")
                self.assertFalse(order.payment_ids)
                self.assertFalse(order.account_move)
                self.assertFalse(self.env["resta.kds.ticket"].search([("pos_order_id", "=", order.id)]))

    def test_failed_accounting_does_not_send_preparation(self):
        self.config.blend_auto_accept = True
        self.mapping.active = False
        order = self._create()
        self._assert_pending_failure(order)
        self.assertFalse(self.env["resta.kds.ticket"].search([("pos_order_id", "=", order.id)]))

    def test_preparation_failure_rolls_back_accounting_then_retry(self):
        self.config.blend_auto_accept = True
        with patch.object(type(self.env["pos.order"]), "_resta_kds_dispatch_one",
                          side_effect=UserError("Preparation unavailable")):
            order = self._create()
        self._assert_pending_failure(order)
        self.assertIn("Preparation unavailable", order.blend_financial_error)
        order.action_blend_accept_order()
        self._assert_done(order)
        self.assertEqual(len(order.payment_ids), 1)
        self.assertEqual(self.env["resta.kds.ticket"].search_count([("pos_order_id", "=", order.id)]), 1)

    def test_global_blend_tax_with_native_per_line_rounding(self):
        self.config.blend_auto_accept = True
        self.env.company.tax_calculation_rounding_method = "round_per_line"
        (self.item | self.modifier).taxes_id = self.tax_fiften_incl
        order = self._create(self._payload(total=76, tax=9.91, base=59, modifier=17))
        self._assert_done(order, 76, 9.92)
        self.assertEqual(order.blend_payload["tax"], 9.91)
        self.assertEqual(self.env.company.tax_calculation_rounding_method, "round_per_line")

    def test_tax_mismatch_uses_odoo_tax_and_keeps_warning(self):
        self.config.blend_auto_accept = True
        self.env.company.tax_calculation_rounding_method = "round_per_line"
        (self.item | self.modifier).taxes_id = self.tax_fiften_incl
        order = self._create(self._payload(total=76, tax=9.90, base=59, modifier=17))
        self._assert_done(order, 76, 9.92)
        self.assertIn("9.90", order.with_context(lang="en_US").blend_tax_warning)
        self.assertIn("9.92", order.with_context(lang="en_US").blend_tax_warning)
        self.assertTrue(self.env["resta.kds.ticket"].search([("pos_order_id", "=", order.id)]))

    def test_six_line_blend_tax_warning_preserves_gross_and_completes_once(self):
        self.config.blend_auto_accept = True
        self.env.company.tax_calculation_rounding_method = "round_per_line"
        self.item.taxes_id = self.tax_fiften_incl
        self.item.modifier_grp_ids = [Command.clear()]
        payload = self._payload(total=294, tax=37.04, subtotal=256.96)
        payload["status"] = "PREPARING"
        prices = [44, 69, 21, 37, 64, 59]
        payload["products"] = [
            {"id": "100", "quantity": 1, "total": price, "modifiers": []} for price in prices
        ]
        order = self._create(payload)
        self.assertEqual(order.blend_financial_state, "done", order.blend_financial_error)
        self.assertEqual(order.blend_status, "preparing")
        self.assertEqual(order.account_move.state, "posted")
        self.assertAlmostEqual(order.amount_paid, 294, places=2)
        self.assertAlmostEqual(order.account_move.amount_total, 294, places=2)
        self.assertAlmostEqual(order.account_move.amount_tax, 38.36, places=2)
        self.assertAlmostEqual(order.account_move.amount_untaxed, 255.64, places=2)
        self.assertEqual(order.account_move.amount_residual, 0)
        self.assertEqual(order.lines.mapped("price_unit"), prices)
        self.assertEqual(order.lines.mapped("price_subtotal"), [38.26, 60, 18.26, 32.17, 55.65, 51.30])
        self.assertEqual(order.lines.mapped("price_subtotal_incl"), prices)
        self.assertFalse(order.blend_financial_error)
        self.assertEqual(order.blend_payload["tax"], 37.04)
        warning = order.with_context(lang="en_US").blend_tax_warning
        for text in ("payload.tax", "37.04", "38.36", "1.32", "294.00", payload["partner_reference"]):
            self.assertIn(text, warning)
        self.assertTrue(order.blend_total_matches)
        self.assertFalse(order.blend_total_warning)
        self.assertFalse(order.message_ids.filtered(lambda message: "payload.tax" in str(message.body)))
        tickets = self.env["resta.kds.ticket"].search([("pos_order_id", "=", order.id)])
        self.assertEqual(len(tickets), 1)
        payment, invoice = order.payment_ids, order.account_move
        self.assertEqual(self._create(payload), order)
        order.action_blend_accept_order()
        self.assertEqual((order.payment_ids, order.account_move), (payment, invoice))
        self.assertFalse(order.message_ids.filtered(lambda message: "payload.tax" in str(message.body)))
        self.assertEqual(self.env["resta.kds.ticket"].search([("pos_order_id", "=", order.id)]), tickets)

    def test_matching_taxes_have_no_warning(self):
        self.config.blend_auto_accept = True
        order = self._create()
        self._assert_done(order)
        self.assertFalse(order.blend_tax_warning)

    def test_tax_warning_does_not_allow_total_mismatch(self):
        (self.item | self.modifier).taxes_id = self.tax_fiften_incl
        order = self._create(self._payload(total=76, tax=9.90, base=59, modifier=17))
        # Preserve line amounts; corrupt only the external total snapshot.
        order.sudo().blend_payload = {**order.blend_payload, "total": 77}
        order.action_blend_accept_order()
        self._assert_pending_failure(order)
        self.assertIn("total", order.blend_financial_error)

    def test_price_excluded_tax_discount_modifiers_and_quantity(self):
        (self.item | self.modifier).taxes_id = self.tax_fiften_excl
        order = self._create(self._payload(total=103.5, tax=13.5, subtotal=100, base=95.5, modifier=4, quantity=2))
        self.assertEqual(order.blend_discount_amount, 10)
        self.assertEqual(order.lines.tax_ids, self.tax_fiften_excl)
        order.action_blend_accept_order()
        self._assert_done(order, 103.5, 13.5)
        # Internal recomputation, as in the sudoed accounting pipeline; ordinary
        # operators cannot rewrite the financial totals through raw RPC.
        order.sudo()._compute_prices()
        self.assertAlmostEqual(order.amount_tax, 13.5, places=2)
        self.assertAlmostEqual(sum(order.lines.mapped("price_subtotal")), 90, places=2)

    def test_price_included_tax(self):
        (self.item | self.modifier).taxes_id = self.tax_fiften_incl
        order = self._create(self._payload(total=115, tax=15, base=103.5, modifier=11.5))
        order.action_blend_accept_order()
        self._assert_done(order, 115, 15)

    def test_different_product_and_modifier_tax_rates(self):
        self.item.taxes_id = self.tax_fiften_excl
        self.modifier.taxes_id = self.tax_five_incl
        order = self._create(self._payload(total=136, tax=16, base=115, modifier=21))
        order.action_blend_accept_order()
        self._assert_done(order, 136, 16)

    def test_fiscal_position_exemption_applies_once(self):
        self.item.taxes_id = self.tax_fiften_excl
        fpos = self.env["account.fiscal.position"].create({
            "name": "Blend exemption", "company_id": self.env.company.id,
        })
        # Odoo 19 maps taxes through original_tax_ids/fiscal_position_ids, not
        # the removed fiscal-position tax mapping model from Odoo 18.
        self.tax_fiften_excl.copy({
            "amount": 0, "fiscal_position_ids": [Command.set(fpos.ids)],
            "original_tax_ids": [Command.set(self.tax_fiften_excl.ids)],
        })
        self.partner_mobt.property_account_position_id = fpos
        order = self._create()
        self.assertEqual(order.fiscal_position_id, fpos)
        order.action_blend_accept_order()
        self._assert_done(order)

    def test_wrong_payload_tax_warns_and_pays_using_configured_tax(self):
        self.item.taxes_id = self.tax_fiften_excl
        order = self._create()
        self.assertEqual(order.state, "draft")
        self.assertTrue(order.blend_tax_warning)
        order.action_blend_accept_order()
        self._assert_done(order, 62, 6.52)
        self.assertTrue(order.blend_tax_warning)

    def test_missing_mapping_then_retry_same_order(self):
        self.mapping.active = False
        self.config.blend_auto_accept = True
        payload = self._payload()
        order = self._create(payload)
        self._assert_pending_failure(order)
        self.mapping.active = True
        self.assertEqual(self._create(payload), order)
        self._assert_pending_failure(order)  # HTTP replay never performs accounting.
        order.action_blend_accept_order()
        self._assert_done(order)
        self.assertEqual(order.blend_financial_attempts, 2)

    def test_no_customer_then_configure_fallback(self):
        self.config.blend_invoice_partner_id = False
        order = self._create()
        self._assert_pending_failure(order)
        self.config.blend_invoice_partner_id = self.partner_mobt
        order.action_blend_accept_order()
        self._assert_done(order)

    def test_actual_customer_takes_precedence(self):
        payload = self._payload()
        payload["customer"] = {"name": "Blend actual buyer", "phone": "customer-" + uuid.uuid4().hex}
        order = self._create(payload)
        order.action_blend_accept_order()
        self._assert_done(order)
        self.assertEqual(order.account_move.partner_id.name, "Blend actual buyer")
        self.assertNotEqual(order.partner_id, self.partner_mobt)

    def test_accept_and_create_replays_do_not_duplicate_financial_records(self):
        payload = self._payload()
        order = self._create(payload)
        order.action_blend_accept_order()
        payment, invoice, picking, log = order.payment_ids, order.account_move, order.picking_ids, self._logs(order)
        for _attempt in range(3):
            order.action_blend_accept_order()
            order._blend_auto_accept()
            self.assertEqual(self._create(payload), order)
        self._assert_done(order)
        self.assertEqual((order.payment_ids, order.account_move, order.picking_ids, self._logs(order)), (payment, invoice, picking, log))

    def test_invoice_failure_rolls_back_payment_and_can_retry(self):
        order = self._create()
        with patch.object(type(order), "_generate_pos_order_invoice", side_effect=UserError("Invoice test failure")):
            order.action_blend_accept_order()
        self._assert_pending_failure(order)
        self.assertFalse(order.picking_ids)
        self.assertFalse(order.to_invoice)
        order.action_blend_accept_order()
        self._assert_done(order)

    def test_failure_after_invoice_rolls_back_invoice_payment_and_outbox(self):
        order = self._create()
        invoices_before = self.env["account.move"].search_count([])
        with patch.object(type(order), "_blend_enqueue_status", side_effect=UserError("Outbox test failure")):
            order.action_blend_accept_order()
        self._assert_pending_failure(order)
        self.assertEqual(self.env["account.move"].search_count([]), invoices_before)
        order.action_blend_accept_order()
        self._assert_done(order)

    def test_database_concurrency_errors_are_not_swallowed(self):
        order = self._create()
        with patch.object(type(order), "_blend_finalize_accounting", side_effect=SerializationFailure("retry request")):
            with self.assertRaises(SerializationFailure), self.env.cr.savepoint():
                order.action_blend_accept_order()
        self.assertFalse(order.payment_ids)

    def test_cancel_after_invoice_requires_review_without_deleting_money(self):
        order = self._create()
        order.action_blend_accept_order()
        payment, invoice = order.payment_ids, order.account_move
        result = self.service.update_order({
            "branch_id": self.config.blend_branch_id, "brand_id": self.config.blend_brand_id,
            "order_id": order.uuid, "status": "CANCELED",
        })
        self.assertTrue(result["success"])
        self.assertEqual(order.blend_status, "cancelled")
        self.assertTrue(order.blend_refund_required)
        self.assertEqual(order.state, "done")
        self.assertEqual(order.payment_ids, payment)
        self.assertEqual(order.account_move, invoice)
        self.assertEqual(invoice.state, "posted")

    def test_rejection_before_acceptance_never_pays(self):
        order = self._create()
        order._blend_apply_manual_decision("rejected", reason="Unavailable")
        self.assertEqual(order.state, "cancel")
        self.assertFalse(order.payment_ids)
        self.assertFalse(order.account_move)

    def test_remote_accepted_status_is_not_an_accounting_command(self):
        order = self._create()
        self.service.update_order({
            "branch_id": self.config.blend_branch_id, "brand_id": self.config.blend_brand_id,
            "order_id": order.uuid, "status": "ACCEPTED",
        })
        self.assertEqual(order.blend_status, "accepted")
        self.assertFalse(order.payment_ids)
        self.assertFalse(order.account_move)
        self.assertFalse(self._logs(order))

    def test_old_order_not_enrolled_by_later_configuration_change(self):
        self.config.blend_finalize_on_accept = False
        order = self._create()
        self.config.blend_finalize_on_accept = True
        order.action_blend_accept_order()
        self.assertFalse(order.blend_financial_required)
        self.assertEqual(order.state, "draft")
        self.assertFalse(order.payment_ids)

    def test_mapping_changes_do_not_rewrite_paid_order(self):
        order = self._create()
        order.action_blend_accept_order()
        self.mapping.payment_method_id = self.cash_payment_method
        order.action_blend_accept_order()
        self.assertEqual(order.blend_payment_method_id, self.bank_payment_method)
        self.assertEqual(order.payment_ids.payment_method_id, self.bank_payment_method)

    def test_unknown_aggregator_never_falls_back_to_cash(self):
        payload = self._payload()
        payload["aggregator"] = "unknown-platform"
        order = self._create(payload)
        order.action_blend_accept_order()
        self._assert_pending_failure(order)

    def test_archived_payment_method_blocks_acceptance(self):
        order = self._create()
        # Native Odoo prevents archiving a method used by an open session. Model
        # the stale configuration explicitly to exercise our runtime guard.
        self.bank_payment_method.flush_recordset()
        self.env.cr.execute("UPDATE pos_payment_method SET active = FALSE WHERE id = %s", [self.bank_payment_method.id])
        self.bank_payment_method.invalidate_recordset(["active"])
        order.action_blend_accept_order()
        self._assert_pending_failure(order)

    def test_partial_payment_does_not_get_silently_topped_up(self):
        order = self._create()
        order.sudo().with_context(blend_skip_status_sync=True).add_payment({
            "pos_order_id": order.id, "payment_method_id": self.bank_payment_method.id, "amount": 10,
        })
        with self.assertRaises(UserError):
            order.action_blend_accept_order()
        self.assertEqual(len(order.payment_ids), 1)
        self.assertEqual(order.amount_paid, 10)
        self.assertFalse(order.account_move)

    def test_outbound_retry_does_not_recreate_payment_or_invoice(self):
        order = self._create()
        order.action_blend_accept_order()
        payment, invoice = order.payment_ids, order.account_move
        log = self._logs(order)
        with patch.object(outbound.requests, "post", return_value=FakeResponse(503, {})):
            log._process_outbound()
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"success": True})):
            log._process_outbound()
        self.assertEqual(order.payment_ids, payment)
        self.assertEqual(order.account_move, invoice)

    def test_mapping_uniqueness_and_pay_later_rejection(self):
        with self.assertRaises(UniqueViolation), self.env.cr.savepoint():
            self.env["blend.payment.mapping"].create({
                "pos_config_id": self.config.id, "aggregator": " jahez ",
                "payment_method_id": self.cash_payment_method.id,
            })
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.mapping.payment_method_id = self.credit_payment_method

    def test_financial_order_does_not_accept_product_outside_menu(self):
        outside = self.env["product.template"].create({
            "name": "Outside Blend menu", "available_in_pos": True, "taxes_id": [Command.clear()],
        })
        payload = self._payload()
        payload["products"][0]["id"] = str(outside.id)
        with self.assertRaises(BlendAPIError):
            self._create(payload)

    def test_pos_operator_can_accept_but_not_configure_or_forge_accounting(self):
        user = new_test_user(self.env, login="blend-accounting-" + uuid.uuid4().hex,
                             groups="base.group_user,point_of_sale.group_pos_user")
        order = self._create()
        with self.assertRaises(AccessError):
            order.with_user(user).write({"blend_financial_required": False})
        with self.assertRaises(AccessError):
            self.mapping.with_user(user).write({"payment_method_id": self.cash_payment_method.id})
        order.with_user(user).action_blend_accept_order()
        self._assert_done(order)
        self.assertEqual(order.blend_decision_user_id, user)

    def test_free_order_has_invoice_but_no_fictitious_payment(self):
        order = self._create(self._payload(total=0, base=0, modifier=0))
        order.action_blend_accept_order()
        self._assert_done(order, 0)

    def test_ordinary_pos_payment_and_invoice_remain_native(self):
        incoming = self._create()
        ordinary = incoming.copy({"to_invoice": True})
        self.assertFalse(ordinary.blend_financial_required)
        self.assertFalse(ordinary.blend_partner_reference)
        self.env["pos.make.payment"].with_context(
            active_id=ordinary.id, active_ids=ordinary.ids, generate_pdf=False,
        ).create({
            "config_id": self.config.id, "amount": ordinary.amount_total,
            "payment_method_id": self.bank_payment_method.id,
        }).check()
        self.assertEqual(ordinary.state, "done")
        self.assertEqual(ordinary.account_move.state, "posted")
        self.assertEqual(len(ordinary.payment_ids), 1)
        self.assertTrue(ordinary.currency_id.is_zero(ordinary.account_move.amount_residual))
        self.assertEqual(incoming.state, "draft")
        self.assertFalse(incoming.payment_ids)
        self.assertFalse(incoming.account_move)

    def test_operator_cannot_substitute_financial_order_or_line_inputs(self):
        user = new_test_user(self.env, login="blend-locked-" + uuid.uuid4().hex,
                             groups="base.group_user,point_of_sale.group_pos_user")
        order = self._create()
        operator_order = order.with_user(user)
        line = operator_order.lines[0]
        for values in (
            {"session_id": self.pos_session.id}, {"partner_id": self.partner_mobt.id},
            {"fiscal_position_id": False}, {"lines": [Command.clear()]},
            {"is_refund": True}, {"date_order": "2020-01-01 00:00:00"},
            {"state": "paid"}, {"amount_paid": order.amount_total},
        ):
            with self.assertRaises(AccessError):
                operator_order.write(values)
        for values in (
            {"product_id": self.modifier.product_variant_id.id},
            {"tax_ids": [Command.set(self.tax_fiften_incl.ids)], "price_unit": line.price_unit},
            {"qty": line.qty * 2, "price_unit": line.price_unit / 2},
            {"order_id": order.id},
        ):
            with self.assertRaises(AccessError):
                line.write(values)
        with self.assertRaises(AccessError):
            line.unlink()
        with self.assertRaises(AccessError):
            line.copy({"order_id": order.id})
        with self.assertRaises(AccessError):
            operator_order.add_payment({"amount": 62, "payment_method_id": self.bank_payment_method.id})
        with self.assertRaises(AccessError):
            self.env["pos.payment"].with_user(user).create({
                "pos_order_id": order.id, "amount": 62, "payment_method_id": self.bank_payment_method.id,
            })
        with self.assertRaises(AccessError):
            self.env["pos.payment"].with_user(user).with_context(default_pos_order_id=order.id).create({
                "amount": 62, "payment_method_id": self.bank_payment_method.id,
            })
        with self.assertRaises(AccessError):
            self.env["pos.order.line"].with_user(user).with_context(default_order_id=order.id).create({
                "product_id": self.item.product_variant_id.id, "qty": 1, "price_unit": 1,
            })
        with self.assertRaises(AccessError):
            self.env["pos.order"].with_user(user).with_context(default_blend_financial_required=True).create({
                "session_id": self.pos_session.id,
            })
        operator_order.action_blend_accept_order()
        self._assert_done(order)
        with self.assertRaises(AccessError):
            operator_order.payment_ids.write({"payment_method_id": self.cash_payment_method.id})
        with self.assertRaises(AccessError):
            operator_order.payment_ids.unlink()
        for values in ({"amount_total": 1}, {"amount_tax": 1}, {"amount_paid": 1}):
            with self.assertRaises(AccessError):
                operator_order.write(values)

    def test_native_refund_is_not_enrolled_or_blocked_by_input_locks(self):
        user = new_test_user(self.env, login="blend-refund-" + uuid.uuid4().hex,
                             groups="base.group_user,point_of_sale.group_pos_manager")
        order = self._create()
        order.with_user(user).action_blend_accept_order()
        refund = order.with_user(user)._refund()
        self.assertFalse(refund.blend_financial_required)
        self.assertFalse(refund.blend_partner_reference)
        self.assertFalse(refund.blend_payment_mapping_id)
        self.assertFalse(refund.blend_taxes_prepared)
        self.assertTrue(all(line.qty < 0 for line in refund.lines))
        self.assertAlmostEqual(refund.amount_total, -order.amount_total, places=2)
        # Native refunds deliberately clear to_invoice; the manager explicitly
        # chooses the standard credit-note workflow, not automatic Blend refund.
        refund.to_invoice = True
        self.env["pos.make.payment"].with_user(user).with_context(
            active_id=refund.id, active_ids=refund.ids, generate_pdf=False,
        ).create({
            "config_id": self.config.id, "amount": refund.amount_total,
            "payment_method_id": self.bank_payment_method.id,
        }).check()
        self.assertEqual(refund.state, "done")
        self.assertEqual(refund.account_move.move_type, "out_refund")
        self.assertEqual(refund.account_move.state, "posted")
        self.assertTrue(refund.currency_id.is_zero(refund.account_move.amount_residual))
        self.assertEqual(len(order.payment_ids), 1)
        self.assertEqual(len(refund.payment_ids), 1)

    def test_remote_accepted_can_complete_locally_without_echo(self):
        for status in ("ACCEPTED", "PREPARING"):
            payload = self._payload()
            payload["status"] = status
            order = self._create(payload)
            self.assertTrue(order.blend_can_finalize)
            self.assertFalse(order.payment_ids)
            order.action_blend_accept_order()
            self.assertEqual(order.blend_financial_state, "done", order.blend_financial_error)
            self.assertEqual(order.blend_status, status.lower())
            self.assertEqual(order.state, "done")
            self.assertFalse(self._logs(order))
            invoice, payment = order.account_move, order.payment_ids
            order.action_blend_accept_order()
            self.assertEqual((order.account_move, order.payment_ids), (invoice, payment))

    def test_session_closed_between_lookup_and_create_is_rejected(self):
        original = type(self.pos_session)._blend_lock_accounting

        def concurrent_close(session):
            session.write({"state": "closed"})
            return original(session)

        payload = self._payload()
        with patch.object(type(self.pos_session), "_blend_lock_accounting", concurrent_close):
            with self.assertRaises(BlendAPIError) as raised:
                self._create(payload)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse(self.env["pos.order"].search([("blend_partner_reference", "=", payload["partner_reference"])]))

    def test_session_no_longer_open_blocks_payment(self):
        order = self._create()
        self.pos_session.state = "closing_control"
        order.action_blend_accept_order()
        self._assert_pending_failure(order)

    def test_import_and_acceptance_write_session_concurrency_fence(self):
        before = self.pos_session.blend_accounting_revision
        order = self._create()
        self.assertGreater(self.pos_session.blend_accounting_revision, before)
        before = self.pos_session.blend_accounting_revision
        order.action_blend_accept_order()
        self._assert_done(order)
        self.assertGreater(self.pos_session.blend_accounting_revision, before)

    def test_uncommitted_accounting_is_invisible_to_other_transaction(self):
        order = self._create()
        order.action_blend_accept_order()
        self._assert_done(order)
        self.env.flush_all()
        with self.registry.cursor() as other_cr:
            for table, record in (("pos_order", order), ("pos_payment", order.payment_ids),
                                  ("account_move", order.account_move), ("blend_integration_log", self._logs(order))):
                other_cr.execute('SELECT id FROM "%s" WHERE id = %%s' % table, [record.id])
                self.assertFalse(other_cr.fetchall())

    def test_session_close_does_not_duplicate_invoice_or_payments(self):
        order = self._create()
        order.action_blend_accept_order()
        self._assert_done(order)
        invoice, payment = order.account_move, order.payment_ids
        self.pos_session.post_closing_cash_details(0)
        self.pos_session.close_session_from_ui()
        self.assertEqual(self.pos_session.state, "closed")
        self.assertEqual((order.account_move, order.payment_ids), (invoice, payment))
        self.assertTrue(invoice.currency_id.is_zero(invoice.amount_residual))
        self.assertEqual(len(self._logs(order)), 1)

    def test_cash_mapping_uses_cash_without_additional_bank_payment(self):
        self.mapping.payment_method_id = self.cash_payment_method
        order = self._create()
        order.action_blend_accept_order()
        self._assert_done(order)
        self.assertEqual(order.payment_ids.payment_method_id, self.cash_payment_method)

    def test_foreign_company_mapping_and_customer_are_rejected(self):
        other = self.setup_other_company(name="Blend accounting other company")
        other_company = other["company"]
        method = self.env["pos.payment.method"].with_company(other_company).create({
            "name": "Other company Blend bank", "company_id": other_company.id,
            "journal_id": other["default_journal_bank"].id,
        })
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.mapping.payment_method_id = method
        partner = self.env["res.partner"].create({"name": "Other company buyer", "company_id": other_company.id})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.config.blend_invoice_partner_id = partner

    def test_other_branch_uses_its_own_payment_mapping(self):
        other_method = self.bank_payment_method.copy({"name": "Second branch bank"})
        second = self.config.copy({
            "name": "Blend second accounting branch", "blend_enabled": True,
            "blend_branch_id": "second-accounting-branch", "blend_brand_id": self.config.blend_brand_id,
            "blend_inbound_token": "second-accounting-test-token", "blend_schema": "pos",
            "blend_status_url": self.config.blend_status_url, "blend_finalize_on_accept": True,
            "blend_invoice_partner_id": self.partner_mobt.id,
            "payment_method_ids": [Command.set(other_method.ids)],
        })
        self.env["blend.payment.mapping"].create({
            "pos_config_id": second.id, "aggregator": "jahez", "payment_method_id": other_method.id,
        })
        # Both branches share the same native menu identities.
        (self.item | self.modifier).write({"blend_config_id": False})
        self.item.modifier_grp_ids.write({"blend_config_id": False})
        second_session = self.env["pos.session"].create({"config_id": second.id})
        second_session.set_opening_control(0, "")
        first_order = self._create()
        payload = self._payload()
        payload["branch_id"] = second.blend_branch_id
        result = BlendPOSAPIService(self.env, self.config | second).create_order(payload)
        second_order = self.env["pos.order"].search([("uuid", "=", result["order_id"])])
        first_order.action_blend_accept_order()
        second_order.action_blend_accept_order()
        self.assertEqual(first_order.payment_ids.payment_method_id, self.bank_payment_method)
        self.assertEqual(second_order.blend_financial_state, "done", second_order.blend_financial_error)
        self.assertEqual(second_order.payment_ids.payment_method_id, other_method)

    def test_stock_movement_is_not_repeated_on_acceptance_retry(self):
        self.item.is_storable = True
        product = self.item.product_variant_id
        location = self.config.picking_type_id.default_location_src_id
        self.env["stock.quant"]._update_available_quantity(product, location, 2)
        self.pos_session.update_stock_at_closing = False
        order = self._create(self._payload(quantity=2, modifier=6))
        order.action_blend_accept_order()
        self._assert_done(order)
        self.assertTrue(order.picking_ids)
        self.assertTrue(all(picking.state == "done" for picking in order.picking_ids))
        self.assertEqual(self.env["stock.quant"]._get_available_quantity(product, location), 0)
        original_pickings = order.picking_ids
        order.action_blend_accept_order()
        self.assertEqual(order.picking_ids, original_pickings)
        self.assertEqual(self.env["stock.quant"]._get_available_quantity(product, location), 0)

    def test_changed_line_cannot_be_paid_for_original_payload_total(self):
        order = self._create()
        line = order.lines[:1].sudo()
        line.price_unit += 1
        order.action_blend_accept_order()
        self._assert_pending_failure(order)

    def test_ordinary_pos_order_not_targeted_by_blend_status_api(self):
        ordinary = self.env["pos.order"].create({
            "session_id": self.pos_session.id, "amount_total": 0, "amount_tax": 0,
            "amount_paid": 0, "amount_return": 0,
        })
        self.assertFalse(ordinary.blend_financial_required)
        with self.assertRaises(BlendAPIError):
            self.service.update_order({
                "branch_id": self.config.blend_branch_id, "brand_id": self.config.blend_brand_id,
                "order_id": ordinary.uuid, "status": "CANCELED",
            })
        self.assertEqual(ordinary.state, "draft")

    def test_portal_cannot_accept_or_read_mapping(self):
        portal = new_test_user(self.env, login="blend-accounting-portal-" + uuid.uuid4().hex, groups="base.group_portal")
        order = self._create()
        with self.assertRaises(AccessError):
            order.with_user(portal).action_blend_accept_order()
        with self.assertRaises(AccessError):
            self.mapping.with_user(portal).read(["payment_method_id"])
        self.assertFalse(order.payment_ids)

    def test_company_b_order_invoice_and_payment_stay_in_company_b(self):
        other = self.setup_other_company(name="Blend invoice company B")
        company = other["company"]
        scoped = self.env(context=dict(self.env.context, allowed_company_ids=[company.id]))
        method = scoped["pos.payment.method"].create({
            "name": "Company B Blend bank", "company_id": company.id,
            "journal_id": other["default_journal_bank"].id,
        })
        partner = scoped["res.partner"].create({"name": "Company B invoice buyer", "company_id": company.id})
        config = scoped["pos.config"].create({
            "name": "Blend company B POS", "company_id": company.id,
            "journal_id": other["default_journal_sale"].id,
            "invoice_journal_id": other["default_journal_sale"].id,
            "payment_method_ids": [Command.set(method.ids)],
            "blend_enabled": True, "blend_inbound_token": "company-b-test-token",
            "blend_branch_id": "company-b-branch", "blend_brand_id": "company-b-brand",
            "blend_schema": "pos", "blend_status_url": "https://blend.example.test/company-b",
            "blend_finalize_on_accept": True, "blend_invoice_partner_id": partner.id,
        })
        scoped["blend.payment.mapping"].create({
            "pos_config_id": config.id, "aggregator": "jahez", "payment_method_id": method.id,
        })
        configure_blend_preset(config)
        BlendMenuService(scoped, config).sync_menu(sample_menu(config.blend_branch_id))
        products = scoped["product.template"].search([("blend_config_id", "=", config.id)])
        products.write({"taxes_id": [Command.clear()], "property_account_income_id": other["default_account_revenue"].id})
        config.write({"limit_categories": True, "iface_available_categ_ids": [Command.set(products.pos_categ_ids.ids)]})
        session = scoped["pos.session"].create({"config_id": config.id})
        session.set_opening_control(0, "")
        payload = self._payload()
        payload.update(branch_id=config.blend_branch_id, brand_id=config.blend_brand_id)
        result = BlendPOSAPIService(scoped, config).create_order(payload)
        order = scoped["pos.order"].search([("uuid", "=", result["order_id"])])
        order.action_blend_accept_order()
        self.assertEqual(order.blend_financial_state, "done", order.blend_financial_error)
        self.assertEqual(order.company_id, company)
        self.assertEqual(order.payment_ids.company_id, company)
        self.assertEqual(order.account_move.company_id, company)
        self.assertEqual(order.account_move.partner_id, partner)
        self.assertEqual(order.payment_ids.payment_method_id, method)
        self.assertEqual(order.account_move.amount_residual, 0)

    def test_mixed_included_and_excluded_taxes_on_same_product(self):
        (self.item | self.modifier).taxes_id = self.tax_five_incl | self.tax_fiften_excl
        order = self._create(self._payload(total=120, tax=20, base=108, modifier=12))
        order.action_blend_accept_order()
        self._assert_done(order, 120, 20)

    def test_global_tax_rounding_and_fractional_unit_price(self):
        self.env.company.tax_calculation_rounding_method = "round_globally"
        (self.item | self.modifier).taxes_id = self.tax_fiften_excl
        order = self._create(self._payload(total=115, tax=15, base=103.5, modifier=11.5 / 3, quantity=3))
        order.action_blend_accept_order()
        self._assert_done(order, 115, 15)

    def test_invoice_rollback_when_native_totals_differ(self):
        order = self._create()
        original = type(order)._generate_pos_order_invoice

        def mismatching_invoice(record):
            result = original(record)
            # Simulate another addon returning an inconsistent posted invoice.
            record.account_move.amount_residual = 1
            return result

        with patch.object(type(order), "_generate_pos_order_invoice", mismatching_invoice):
            order.action_blend_accept_order()
        self._assert_pending_failure(order)

    def test_disabling_feature_blocks_pending_financial_order_but_not_history(self):
        order = self._create()
        self.config.blend_finalize_on_accept = False
        order.action_blend_accept_order()
        self._assert_pending_failure(order)
