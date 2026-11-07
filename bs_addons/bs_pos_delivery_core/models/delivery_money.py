from odoo import Command, _, models
from odoo.exceptions import UserError


class DeliveryMoneyEngine(models.AbstractModel):
    _name = "bs.delivery.money.engine"
    _description = "Delivery Amount Reconciliation"

    def _price_unit_from_gross(self, order, product, taxes, amount, quantity=1.0):
        """Convert a gross amount to the unit price expected by Odoo's taxes."""
        details = taxes._get_tax_details(
            amount / quantity,
            quantity,
            precision_rounding=order.currency_id.rounding,
            rounding_method="round_globally",
            product=product,
            special_mode="total_included",
        )
        included = sum(
            item["tax_amount"]
            for item in details["taxes_data"]
            if item["tax"].price_include
        )
        return (details["total_excluded"] + included) / quantity

    def _prepare_fee_line(self, order, product, amount, label):
        taxes = product.taxes_id.filtered(
            lambda tax: not tax.company_id or tax.company_id == order.company_id,
        )
        effective_taxes = order.store_binding_id.fiscal_position_id.map_tax(taxes)
        return Command.create(
            {
                "product_id": product.id,
                "name": label,
                "full_product_name": label,
                "qty": 1.0,
                "price_unit": self._price_unit_from_gross(
                    order, product, effective_taxes, amount,
                ),
                # Keep the original taxes: POS applies the fiscal position itself.
                "tax_ids": [Command.set(taxes.ids)],
            },
        )

    def _pos_line_tax_totals(self, order, commands):
        """Compute the same rounded tax summary used to create POS lines."""
        order.ensure_one()
        tax_model = self.env["account.tax"].with_company(order.company_id)
        base_lines = []
        values_list = []
        for command in commands:
            values = dict(command[2])
            product = self.env["product.product"].browse(values["product_id"])
            tax_ids = values["tax_ids"][0][2]
            taxes = tax_model.browse(tax_ids)
            effective_taxes = order.store_binding_id.fiscal_position_id.map_tax(taxes)
            base_lines.append(
                tax_model._prepare_base_line_for_taxes_computation(
                    None,
                    currency_id=order.currency_id,
                    product_id=product,
                    tax_ids=effective_taxes,
                    price_unit=values["price_unit"],
                    quantity=values["qty"],
                    discount=values.get("discount", 0.0),
                ),
            )
            values_list.append(values)
        tax_model._add_tax_details_in_base_lines(base_lines, order.company_id)
        tax_model._round_base_lines_tax_details(base_lines, order.company_id)
        totals = tax_model._get_tax_totals_summary(
            base_lines=base_lines,
            currency=order.currency_id,
            company=order.company_id,
        )
        return values_list, base_lines, totals

    def _reconcile_pos_lines(self, order, commands):
        """Validate provider totals before creating a POS order or any payment."""
        values_list, base_lines, totals = self._pos_line_tax_totals(order, commands)
        odoo_tax = totals["tax_amount_currency"]
        if (not order._compare_provider_financial_amounts(
                order.amount_total, totals["total_amount_currency"])
                and order._compare_provider_financial_amounts(order.vat_amount, odoo_tax)
                and order._is_tax_rounding_difference(order.vat_amount, odoo_tax)):
            order._accept_tax_rounding_difference(odoo_tax)
        for expected, actual, label in (
            (order.amount_total, totals["total_amount_currency"], _("Total")),
            (order.vat_amount, odoo_tax, _("Tax")),
        ):
            if order._compare_provider_financial_amounts(expected, actual):
                raise UserError(
                    _(
                        "Delivery financial review required: %(label)s from the "
                        "provider is %(expected)s, but Odoo computes %(actual)s. "
                        "Check item prices, discounts, fees, taxes and fiscal position.",
                        label=label,
                        expected=expected,
                        actual=actual,
                    ),
                )
        result = []
        for values, base_line in zip(values_list, base_lines, strict=True):
            details = base_line["tax_details"]
            values["price_subtotal"] = details["total_excluded_currency"]
            values["price_subtotal_incl"] = details["total_included_currency"]
            result.append(Command.create(values))
        return result
