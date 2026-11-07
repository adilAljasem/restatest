import math

from odoo import _, api, Command, fields, models
from odoo.exceptions import ValidationError


DISCOUNT_XMLID = "bs_pos_blend_intgeration.product_blend_order_discount"


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _blend_add_import_discount_line(self, amount):
        self.ensure_one()
        product = self.env.ref(DISCOUNT_XMLID)
        if not product.active or product.type != "service":
            raise ValidationError(_("The Blend promotion discount product must be an active service."))
        return self.env["pos.order.line"].create({
            "order_id": self.id, "product_id": product.id, "qty": 1,
            "price_unit": -amount, "price_subtotal": -amount, "price_subtotal_incl": -amount,
            "blend_gross_total": -amount, "price_type": "manual",
            "tax_ids": [Command.clear()], "full_product_name": product.name,
        })

    def _blend_prepare_discount_taxes(self):
        """Use native global-discount rounding, retaining each source tax group."""
        self.ensure_one()
        discounts = self.lines.filtered("blend_is_discount_line")
        if not discounts:
            return
        product = self.env.ref(DISCOUNT_XMLID)
        sales = self.lines - discounts
        tax_model = self.env["account.tax"]
        base_lines = sales._prepare_tax_base_line_values()
        tax_model._add_tax_details_in_base_lines(base_lines, self.company_id)
        tax_model._round_base_lines_tax_details(base_lines, self.company_id)
        bases = tax_model._prepare_global_discount_lines(
            base_lines, self.company_id, "fixed", self.blend_discount_amount,
            grouping_function=lambda base: {"product_id": product},
        )
        original_taxes = {}
        for line in sales:
            key = tuple(sorted(line.tax_ids_after_fiscal_position.ids))
            original_taxes[key] = original_taxes.get(key, tax_model.browse()) | line.tax_ids
        values = []
        for base in bases:
            key = tuple(sorted(base["tax_ids"].ids))
            if key not in original_taxes:
                raise ValidationError(_("The Blend promotion requires review of its discountable tax groups."))
            details = base["tax_details"]
            values.append({
                "order_id": self.id, "product_id": product.id, "qty": base["quantity"],
                "price_unit": base["price_unit"], "discount": 0,
                "tax_ids": [Command.set(original_taxes[key].ids)],
                "price_subtotal": details["total_excluded_currency"],
                "price_subtotal_incl": details["total_included_currency"],
                "blend_gross_total": details["total_included_currency"],
                "extra_tax_data": tax_model._export_base_line_extra_tax_data(base),
                "price_type": "manual", "full_product_name": product.name,
            })
        if not self.currency_id.is_zero(sum(v["blend_gross_total"] for v in values) + self.blend_discount_amount):
            raise ValidationError(_("The Blend promotion discount could not be represented at currency precision."))
        discounts.unlink()
        self.env["pos.order.line"].create(values)

    def _blend_check_refund_discount(self):
        """Do not pay a refund at undiscounted prices or refund the full coupon twice."""
        for refund in self:
            source_orders = (
                refund.lines.refunded_orderline_id.order_id | refund.lines.blend_refund_discount_order_id
            ).filtered("blend_is_inbound")
            for source in source_orders:
                coupons = source.lines.filtered("blend_is_discount_line")
                if not coupons:
                    continue
                refunded_lines = refund.lines.filtered(lambda line: line.refunded_orderline_id.order_id == source)
                if any(line.qty > 0 for line in refunded_lines):
                    raise ValidationError(_("Blend refund quantities must be negative."))
                for coupon in coupons:
                    key = set(coupon.tax_ids_after_fiscal_position.ids)
                    originals = (source.lines - coupons).filtered(
                        lambda line: set(line.tax_ids_after_fiscal_position.ids) == key
                    )
                    gross = sum(originals.mapped("blend_gross_total"))
                    selected_gross = sum(
                        abs(line.qty / line.refunded_orderline_id.qty) * line.refunded_orderline_id.blend_gross_total
                        for line in refunded_lines if line.refunded_orderline_id in originals
                    )
                    expected = refund.currency_id.round(selected_gross / gross * -coupon.blend_gross_total) if gross else 0
                    adjustments = refunded_lines.filtered(lambda line: line.refunded_orderline_id == coupon)
                    adjustments |= refund.lines.filtered(
                        lambda line: line.blend_is_discount_line and line.blend_refund_discount_order_id == source
                        and set(line.tax_ids_after_fiscal_position.ids) == key
                    )
                    actual = sum(
                        line._compute_amount_line_all()["price_subtotal_incl"] * (-1 if refund.is_refund else 1)
                        for line in adjustments
                    )
                    if not refund.currency_id.is_zero(expected - actual):
                        raise ValidationError(_(
                            "This Blend refund must include its proportional promotion discount (%(amount)s). "
                            "Review the discount refund line before recording payment.", amount=expected,
                        ))

    def blend_refund_discount_data(self, quantities):
        """Read-only POS helper: calculate the coupon share of selected returns."""
        self.ensure_one()
        self.check_access("read")
        if not isinstance(quantities, dict):
            raise ValidationError(_("Invalid Blend refund quantities."))
        sales = self.lines.filtered(lambda line: not line.blend_is_discount_line)
        quantities_by_id = {}
        for key, value in quantities.items():
            try:
                line_id, quantity = int(key), float(value)
            except (TypeError, ValueError, OverflowError):
                raise ValidationError(_("Invalid Blend refund quantities.")) from None
            source = sales.filtered(lambda line: line.id == line_id)
            if not source or not math.isfinite(quantity) or quantity < 0 or quantity > source.qty:
                raise ValidationError(_("Invalid Blend refund quantities."))
            quantities_by_id[line_id] = quantity
        values = []
        for coupon in self.lines.filtered("blend_is_discount_line"):
            originals = sales.filtered(lambda line: line.tax_ids_after_fiscal_position == coupon.tax_ids_after_fiscal_position)
            gross = sum(originals.mapped("blend_gross_total"))
            selected = sum(quantities_by_id.get(line.id, 0) / line.qty * line.blend_gross_total for line in originals)
            amount = self.currency_id.round(selected / gross * -coupon.blend_gross_total) if gross else 0
            if self.currency_id.is_zero(amount):
                continue
            taxes = coupon.tax_ids_after_fiscal_position
            details = taxes._get_tax_details(-amount, 1, special_mode="total_included", rounding_method="round_globally")
            net = details["total_excluded"]
            details = taxes._get_tax_details(net, 1, special_mode="total_excluded", rounding_method="round_globally")
            unit = net + sum(t["tax_amount"] for t in details["taxes_data"] if t["tax"].price_include)
            values.append({"product_id": coupon.product_id.id, "qty": -1, "price_unit": unit,
                           "tax_ids": coupon.tax_ids.ids, "blend_refund_discount_order_id": self.id,
                           "full_product_name": coupon.product_id.name, "expected_amount": amount})
        return values

    def _blend_sync_refund_discounts(self):
        for refund in self:
            sources = refund.lines.refunded_orderline_id.order_id.filtered(
                lambda source: source.blend_is_inbound and source.lines.filtered("blend_is_discount_line")
            )
            for source in sources:
                quantities = {}
                for line in refund.lines:
                    original = line.refunded_orderline_id
                    if original.order_id == source and not original.blend_is_discount_line:
                        quantities[str(original.id)] = quantities.get(str(original.id), 0) + abs(line.qty)
                values = source.blend_refund_discount_data(quantities)
                refund.lines.filtered(lambda line: line.blend_is_discount_line and (
                    line.refunded_orderline_id.order_id == source or line.blend_refund_discount_order_id == source
                )).unlink()
                for vals in values:
                    vals.pop("expected_amount")
                    vals.update(order_id=refund.id, price_type="manual", price_subtotal=0, price_subtotal_incl=0,
                                tax_ids=[Command.set(vals["tax_ids"])])
                    line = self.env["pos.order.line"].create(vals)
                    line.write(line._compute_amount_line_all())
            refund._compute_prices()

    def _refund(self):
        refunds = super()._refund()
        refunds.filtered(lambda refund: refund.lines.refunded_orderline_id.order_id.lines.filtered("blend_is_discount_line"))._blend_sync_refund_discounts()
        return refunds

    def action_pos_order_paid(self):
        self._blend_check_refund_discount()
        return super().action_pos_order_paid()

    def _process_saved_order(self, draft):
        if not draft:
            self._blend_check_refund_discount()
        return super()._process_saved_order(draft)


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    blend_is_discount_line = fields.Boolean(compute="_compute_blend_is_discount_line")
    blend_refund_discount_order_id = fields.Many2one("pos.order", copy=False, readonly=True, check_company=True)

    @api.depends("product_id")
    def _compute_blend_is_discount_line(self):
        product = self.env.ref(DISCOUNT_XMLID, raise_if_not_found=False)
        for line in self:
            line.blend_is_discount_line = bool(product and line.product_id == product)

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + ["blend_is_discount_line", "blend_refund_discount_order_id"]

    def _compute_amount_line_all(self):
        if not self.blend_is_discount_line:
            return super()._compute_amount_line_all()
        # Unlike compute_all, the native base-line engine retains the global
        # discount's penny adjustments in extra_tax_data, including on refunds.
        tax_model = self.env["account.tax"]
        bases = [self._prepare_base_line_for_taxes_computation()]
        tax_model._add_tax_details_in_base_lines(bases, self.order_id.company_id)
        tax_model._round_base_lines_tax_details(bases, self.order_id.company_id)
        details = bases[0]["tax_details"]
        return {"price_subtotal": details["total_excluded_currency"],
                "price_subtotal_incl": details["total_included_currency"]}


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    def _get_discount_lines(self):
        product = self.env.ref(DISCOUNT_XMLID, raise_if_not_found=False)
        return super()._get_discount_lines() | self.filtered(lambda line: product and line.product_id == product)


class ProductTemplate(models.Model):
    _inherit = "product.template"

    @api.model
    def _load_pos_data_read(self, records, config):
        data = super()._load_pos_data_read(records, config)
        product = self.env.ref(DISCOUNT_XMLID, raise_if_not_found=False)
        if config.blend_enabled and product and product.product_tmpl_id.id not in {row["id"] for row in data}:
            data.extend(product.product_tmpl_id.read(self._load_pos_data_fields(config), load=False))
        return data
