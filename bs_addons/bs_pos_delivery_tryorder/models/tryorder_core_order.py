"""Preserve native instruction modifiers on POS lines created by Delivery Core."""

from odoo import Command, _, api, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class TryOrderDeliveryOrderService(models.AbstractModel):
    _inherit = "bs.delivery.order.service"

    @api.model
    def _line_values(self, connection, store, line, sequence):
        reference = line["external_product_id"]
        if connection.provider_id.code == "tryorder" and reference.startswith("posinstr-"):
            return {
                "sequence": sequence * 10,
                "external_line_id": line["external_line_id"],
                "external_product_id": reference,
                "external_sku": reference,
                "name": line["name"],
                "product_id": False,
                "quantity": line["quantity"],
                "price_unit": 0.0,
                "discount_amount": 0.0,
                "vat_amount": 0.0,
                "price_subtotal": 0.0,
                "customer_note": line.get("note"),
            }
        return super()._line_values(connection, store, line, sequence)


class TryOrderDeliveryOrder(models.Model):
    _inherit = "bs.delivery.order"

    def _pos_order_preset(self, store):
        """Every TryOrder order is a delivery order on the POS, never Dine In.

        TryOrder sends pickup, delivery and dine-in order types, but all of
        them reach the branch through the app and are handled as delivery
        orders (POS Delivery tab, kitchen ticket "Type: Delivery").
        """
        if self.provider_id.code != "tryorder" or not store:
            return super()._pos_order_preset(store)
        preset = store.delivery_preset_id
        if preset and store._is_usable_delivery_preset(preset):
            return preset
        return store._auto_delivery_preset() or super()._pos_order_preset(store)

    def _pos_order_pricelist(self, store):
        pricelist = super()._pos_order_pricelist(store)
        if self.provider_id.code == "tryorder":
            return self.connection_id._get_menu_pricelist() or pricelist
        return pricelist

    def _compare_provider_financial_amounts(self, expected, actual):
        self.ensure_one()
        if self.provider_id.code == "tryorder":
            return float_compare(expected, actual, precision_digits=1)
        return super()._compare_provider_financial_amounts(expected, actual)

    def _append_operational_amount_lines(self, commands, current_total, store):
        current_total = super()._append_operational_amount_lines(
            commands, current_total, store,
        )
        if self.provider_id.code != "tryorder":
            return current_total
        _values, _base_lines, totals = self.env[
            "bs.delivery.money.engine"
        ]._pos_line_tax_totals(self, commands)
        actual_total = totals["total_amount_currency"]
        if (not self.currency_id.compare_amounts(self.amount_total, actual_total)
                or self._compare_provider_financial_amounts(self.amount_total, actual_total)
                or self._compare_provider_financial_amounts(
                    self.vat_amount, totals["tax_amount_currency"],
                )):
            return current_total
        adjustment = self.currency_id.round(self.amount_total - actual_total)
        if adjustment:
            commands.append(self._pos_adjustment_command(
                self._tryorder_rounding_product(), adjustment,
                _("TryOrder rounding adjustment"), self.env["account.tax"],
            ))
            current_total += adjustment
        return current_total

    def _tryorder_rounding_product(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", (194020, self.company_id.id),
        )
        Product = self.env["product.product"].sudo().with_company(self.company_id)
        code = "BS-TRYORDER-ROUNDING-%s" % self.company_id.id
        product = Product.search([
            ("default_code", "=", code), ("company_id", "=", self.company_id.id),
        ], limit=1)
        if not product:
            product = Product.create({
                "name": _("TryOrder Rounding Adjustment"),
                "default_code": code,
                "company_id": self.company_id.id,
                "type": "service",
                "sale_ok": False,
                "purchase_ok": False,
                "available_in_pos": False,
                "taxes_id": [Command.clear()],
                "list_price": 0.0,
                "responsible_id": False,
            })
        if (product.taxes_id or product.type != "service"
                or product.sale_ok or product.available_in_pos):
            raise UserError(_("TryOrder rounding product must be an untaxed hidden service."))
        return product

    def _untaxed_discount_line_taxes(self):
        taxes = super()._untaxed_discount_line_taxes()
        if self.provider_id.code != "tryorder" or self.company_id.country_id.code != "SA":
            return taxes
        Tax = self.env["account.tax"].with_company(self.company_id)
        if "l10n_sa_exemption_reason_code" not in Tax._fields:
            return taxes
        tax = Tax.search([
            ("company_id", "=", self.company_id.id),
            ("type_tax_use", "=", "sale"),
            ("amount_type", "=", "percent"),
            ("amount", "=", 0),
            ("l10n_sa_exemption_reason_code", "=", "VATEX-SA-OOS"),
        ], limit=1)
        if not tax:
            raise UserError(_(
                "Configure an active Saudi out-of-scope 0%% sales tax "
                "(VATEX-SA-OOS) for TryOrder discounts in %(company)s.",
                company=self.company_id.display_name,
            ))
        fiscal_position = self.store_binding_id.fiscal_position_id
        mapped = fiscal_position.map_tax(tax) if fiscal_position else tax
        if (len(mapped) != 1 or mapped.amount or mapped.amount_type != "percent"
                or mapped.l10n_sa_exemption_reason_code != "VATEX-SA-OOS"):
            raise UserError(_(
                "The POS fiscal position must preserve the Saudi out-of-scope "
                "0% tax on TryOrder discounts."
            ))
        return mapped

    def _pos_order_line_price(self, line, taxes):
        original = super()._pos_order_line_price(line, taxes)
        if self.provider_id.code != "tryorder":
            return original
        if line.product_id.type == "combo" and line.modifier_line_ids.filtered(
            lambda child: child.external_product_id.startswith("comboitem-")
        ):
            return 0.0
        if not line.parent_line_id or not line.external_product_id.startswith("comboitem-"):
            effective_taxes = self.store_binding_id.fiscal_position_id.map_tax(taxes)
            if any(tax.price_include for tax in effective_taxes):
                return self.env["bs.delivery.money.engine"]._price_unit_from_gross(
                    self, line.product_id, effective_taxes,
                    line.price_subtotal + line.vat_amount,
                    quantity=line.quantity,
                )
            return original
        siblings = line.parent_line_id.modifier_line_ids.filtered(
            lambda child: child.external_product_id.startswith("comboitem-")
        ).sorted(lambda child: child.id)
        if not siblings:
            return original
        parent_total = line.parent_line_id.price_subtotal + line.parent_line_id.vat_amount
        total_weights = sum(
            sibling.price_subtotal + sibling.vat_amount for sibling in siblings
        )
        allocations = []
        remaining = parent_total
        for sibling in siblings[:-1]:
            portion = self.currency_id.round(
                parent_total * (
                    (sibling.price_subtotal + sibling.vat_amount) / total_weights
                    if total_weights else 1 / len(siblings)
                )
            )
            allocations.append(portion)
            remaining -= portion
        allocations.append(remaining)
        target_gross = (
            line.price_subtotal + line.vat_amount
            + allocations[list(siblings).index(line)]
        )
        effective_taxes = self.store_binding_id.fiscal_position_id.map_tax(taxes)
        return self.env["bs.delivery.money.engine"]._price_unit_from_gross(
            self, line.product_id, effective_taxes, target_gross,
            quantity=line.quantity,
        )

    def _link_pos_order_modifiers_and_combos(self, pos_order, delivery_pos_line_map):
        result = super()._link_pos_order_modifiers_and_combos(
            pos_order, delivery_pos_line_map,
        )
        if self.provider_id.code != "tryorder":
            return result
        instructions_by_parent = {}
        for line in self.line_ids.filtered(
            lambda row: row.is_modifier and row.parent_line_id
            and row.external_product_id.startswith("posinstr-")
        ):
            parent = delivery_pos_line_map.get(line.parent_line_id.id)
            if not parent:
                continue
            option_id = int(line.external_product_id[9:])
            option = self.env["pos.modifier.option"].sudo().browse(option_id)
            quantity = line.quantity / parent.qty
            instructions_by_parent.setdefault(parent, []).append({
                "option_id": option.id,
                "quantity": quantity,
                "name": line.name,
                "kitchen_name": option.kitchen_name or line.name,
                "group_name": option.group_id.name,
            })
        for parent, instructions in instructions_by_parent.items():
            selections = dict(parent.modifier_selection_data or {})
            for instruction in instructions:
                key = str(instruction["option_id"])
                selections[key] = selections.get(key, 0) + instruction["quantity"]
            parent.write({
                "modifier_selection_data": selections,
                "modifier_instruction_data": {"instructions": instructions},
            })
        return result
