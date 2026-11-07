import copy
import json
import logging
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .test_order_accounting import BlendAccountingCommon


_logger = logging.getLogger(__name__)
NINJA_PAYLOAD = {
    "tax": "0.00", "note": None, "total": "141.40", "status": "PENDING",
    "brand_id": "1300011", "customer": {"name": "NINJA", "phone": "555550005"},
    "products": [
        {"id": "859", "note": None, "total": 126, "quantity": 2, "modifiers": []},
        {"id": "851", "note": None, "total": 76, "quantity": 1, "modifiers": []},
    ],
    "subtotal": "202.00", "branch_id": "1300024", "aggregator": "ninja",
    "order_type": "delivery", "partner_reference": "ninja: 201346117",
    "aggregator_order_id": "201346117",
}


@tagged("post_install", "-at_install", "blend_discount")
class TestBlendOrderDiscount(BlendAccountingCommon):
    def setUp(self):
        super().setUp()
        self.config.write({"blend_brand_id": "1300011", "blend_branch_id": "1300024"})
        self.item.write({"blend_external_id": "859", "name": "Margarita Pizza",
                         "modifier_grp_ids": [Command.clear()]})
        self.second = self.item.copy({"blend_external_id": "851", "name": "Grilled Chicken Pizza"})
        self.mapping.aggregator = "ninja"

    def _incoming(self, payload=None):
        return self._create(copy.deepcopy(payload or NINJA_PAYLOAD))

    def test_exact_ninja_payload_invoice_and_replay(self):
        with patch("requests.sessions.Session.request", side_effect=AssertionError("No remote I/O allowed")):
            order = self._incoming()
            self.assertEqual(order.blend_payload, NINJA_PAYLOAD)
            self.assertEqual(len(order.lines), 3)
            sale_lines = order.lines.filtered(lambda line: not line.blend_is_discount_line)
            discount = order.lines.filtered("blend_is_discount_line")
            self.assertEqual(sorted(sale_lines.mapped("qty")), [1, 2])
            self.assertEqual(sorted(sale_lines.mapped("price_subtotal_incl")), [76, 126])
            self.assertAlmostEqual(discount.price_subtotal_incl, -60.6, places=2)
            self.assertTrue(order.blend_total_matches)
            self.assertAlmostEqual(order.amount_total, 141.4, places=2)
            order.action_blend_accept_order()
            self._assert_done(order, total=141.4)
            self.assertEqual(order, self._incoming())
            self.assertEqual(len(order.lines), 3)
            self.assertEqual(len(order.account_move.invoice_line_ids.filtered("product_id")), 3)
            self.assertEqual(order.account_move.invoice_line_ids._get_discount_lines().product_id, discount.product_id)
            self.assertFalse(order.picking_ids.move_ids.filtered(lambda move: move.product_id == discount.product_id))
            if "account.edi.xml.ubl_21.zatca" in self.env:
                bases, _tax_lines = order.account_move._get_rounded_base_and_tax_lines()
                exporter = self.env["account.edi.xml.ubl_21.zatca"]
                allowances = [base for base in bases if exporter._is_document_allowance_charge(base)]
                self.assertEqual(len(allowances), 1)
                self.assertAlmostEqual(allowances[0]["tax_details"]["total_excluded_currency"], -60.6, places=2)
            _logger.info("BLEND_EXACT_PAYLOAD_RESULT %s", json.dumps({
                "reference": order.blend_partner_reference,
                "lines": [[line.product_id.name, line.qty, line.price_subtotal_incl] for line in order.lines],
                "discount": order.blend_discount_amount, "tax": order.amount_tax,
                "total": order.amount_total, "invoice_total": order.account_move.amount_total,
                "paid": order.amount_paid, "matches": order.blend_total_matches,
            }))

    def test_already_discounted_lines_do_not_get_a_second_discount(self):
        payload = copy.deepcopy(NINJA_PAYLOAD)
        payload["products"][0]["total"] = 88.2
        payload["products"][1]["total"] = 53.2
        order = self._incoming(payload)
        self.assertFalse(order.lines.filtered("blend_is_discount_line"))
        self.assertAlmostEqual(order.amount_total, 141.4, places=2)

    def test_unexplained_mismatch_is_still_rejected(self):
        payload = copy.deepcopy(NINJA_PAYLOAD)
        payload["products"][0]["total"] = 125
        from ..services.exceptions import BlendAPIError
        with self.assertRaises(BlendAPIError):
            self._incoming(payload)

    def test_discount_does_not_print_or_warn_about_missing_kitchen_printer(self):
        printer = self.env["pos.printer"].create({
            "name": "Pizza kitchen", "company_id": self.config.company_id.id,
            "product_categories_ids": [Command.set(self.item.pos_categ_ids.ids)],
        })
        self.config.printer_ids = [Command.set(printer.ids)]
        order = self._incoming()
        order.action_blend_accept_order()
        self.assertFalse(order.blend_kitchen_print_warning)
        self.assertEqual(len(order._blend_kitchen_lines(printer)), 2)

    def test_native_tax_groups_and_invoice_keep_final_total(self):
        self.item.taxes_id = self.tax_fiften_excl
        order = self._incoming()
        discounts = order.lines.filtered("blend_is_discount_line")
        self.assertEqual(len(discounts), 2)
        self.assertAlmostEqual(sum(discounts.mapped("price_subtotal_incl")), -60.6, places=2)
        self.assertAlmostEqual(order.amount_total, 141.4, places=2)
        order.action_blend_accept_order()
        self.assertEqual(order.blend_financial_state, "done", order.blend_financial_error)
        self.assertAlmostEqual(order.account_move.amount_total, 141.4, places=2)

    def test_full_discount_and_native_full_refund(self):
        order = self._incoming()
        order.action_blend_accept_order()
        refund = order._refund()
        self.assertAlmostEqual(refund.amount_total, -141.4, places=2)
        refund._blend_check_refund_discount()
        payload = copy.deepcopy(NINJA_PAYLOAD)
        payload.update(partner_reference="ninja: free-test", total="0.00")
        free = self._incoming(payload)
        self.assertTrue(free.currency_id.is_zero(free.amount_total))
        self.assertAlmostEqual(free.lines.filtered("blend_is_discount_line").price_subtotal_incl, -202, places=2)

    def test_partial_refund_requires_only_its_share_of_discount(self):
        order = self._incoming()
        order.action_blend_accept_order()
        refund = order._refund()
        pizza = refund.lines.filtered(lambda line: line.product_id == self.item.product_variant_id)
        refund.lines.filtered(lambda line: line.product_id == self.second.product_variant_id).unlink()
        pizza.qty = -1
        refund._blend_sync_refund_discounts()
        discount = refund.lines.filtered("blend_is_discount_line")
        self.assertAlmostEqual(refund.amount_total, -44.1, places=2)
        refund._blend_check_refund_discount()
        discount.qty = 0
        with self.assertRaises(ValidationError):
            refund._blend_check_refund_discount()
        discount.write({"qty": 1, "price_unit": -18.9})
        with self.assertRaises(ValidationError):
            refund._blend_check_refund_discount()
        discount.qty = -1
        refund.lines.filtered(lambda line: not line.blend_is_discount_line).unlink()
        with self.assertRaises(ValidationError):
            refund._blend_check_refund_discount()

    def test_rounding_and_fiscal_position_mapping(self):
        self.item.taxes_id = self.tax_fiften_excl
        fiscal_position = self.env["account.fiscal.position"].create({
            "name": "Blend promotion mapping", "company_id": self.env.company.id,
        })
        replacement = self.tax_fiften_excl.copy({
            "name": "Mapped 10 percent", "amount": 10,
            "fiscal_position_ids": [Command.set(fiscal_position.ids)],
            "original_tax_ids": [Command.set(self.tax_fiften_excl.ids)],
        })
        self.config.default_fiscal_position_id = fiscal_position
        payload = copy.deepcopy(NINJA_PAYLOAD)
        payload["total"] = "141.39"
        order = self._incoming(payload)
        order.action_blend_accept_order()
        self.assertEqual(order.blend_financial_state, "done", order.blend_financial_error)
        self.assertAlmostEqual(order.amount_total, 141.39, places=2)
        self.assertAlmostEqual(order.account_move.amount_total, 141.39, places=2)
        discount = order.lines.filtered(lambda line: line.blend_is_discount_line and line.tax_ids)
        self.assertEqual(discount.tax_ids, self.tax_fiften_excl)
        self.assertEqual(discount.tax_ids_after_fiscal_position, replacement)

    def test_refund_helper_checks_quantities(self):
        order = self._incoming()
        pizza = order.lines.filtered(lambda line: line.product_id == self.item.product_variant_id)
        values = order.blend_refund_discount_data({str(pizza.id): 1})
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["qty"], -1)
        self.assertAlmostEqual(values[0]["price_unit"], -18.9, places=2)
        for quantities in ({str(pizza.id): 3}, {str(pizza.id): "NaN"}, {"-1": 1}):
            with self.assertRaises(ValidationError):
                order.blend_refund_discount_data(quantities)

    def test_taxed_partial_refund_payment_and_credit_note(self):
        (self.item | self.second).taxes_id = self.tax_fiften_excl
        order = self._incoming()
        order.action_blend_accept_order()
        self._assert_done(order, total=141.4, tax=18.44)
        refund = order._refund()
        refund.lines.filtered(lambda line: line.product_id == self.second.product_variant_id).unlink()
        pizza = refund.lines.filtered(lambda line: line.product_id == self.item.product_variant_id)
        pizza.qty = -1
        pizza.write(pizza._compute_amount_line_all())
        refund._blend_sync_refund_discounts()
        self.assertAlmostEqual(refund.amount_total, -44.1, places=2)
        refund.to_invoice = True
        self.env["pos.make.payment"].with_context(
            active_model="pos.order", active_id=refund.id, active_ids=refund.ids, generate_pdf=False,
        ).create({"config_id": self.config.id, "amount": refund.amount_total,
                  "payment_method_id": self.bank_payment_method.id}).check()
        self.assertEqual(refund.state, "done")
        self.assertEqual(refund.account_move.move_type, "out_refund")
        self.assertAlmostEqual(refund.account_move.amount_total, 44.1, places=2)
        self.assertAlmostEqual(refund.account_move.amount_residual, 0, places=2)
        self.assertAlmostEqual(refund.amount_paid, -44.1, places=2)

    def test_hidden_discount_product_is_loaded_in_blend_pos(self):
        product = self.env.ref("bs_pos_blend_intgeration.product_blend_order_discount")
        # Global installation data is configured as admin; the actual loader
        # below still runs as the test's restricted accounting/POS user.
        product.sudo().available_in_pos = False
        templates = self.env["product.template"]
        rows = templates._load_pos_data_read(templates.browse(), self.config)
        self.assertIn(product.product_tmpl_id.id, [row["id"] for row in rows])
        line_fields = self.env["pos.order.line"]._load_pos_data_fields(self.config)
        self.assertIn("blend_refund_discount_order_id", line_fields)
        self.assertIn("blend_is_discount_line", line_fields)
