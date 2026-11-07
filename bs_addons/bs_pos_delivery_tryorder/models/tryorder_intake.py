"""Authenticate TryOrder requests and translate them into Delivery Core v1."""

import hashlib
import json
import logging
import re
import secrets
from datetime import timezone
from decimal import Decimal, InvalidOperation

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.bs_pos_delivery_core.exceptions import DeliveryBusinessError
from odoo.addons.bs_pos_delivery_core.tools import serialize_masked_payload

_logger = logging.getLogger(__name__)


class TryOrderRequestError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


class TryOrderIntake(models.AbstractModel):
    _name = "bs.delivery.tryorder.intake"
    _description = "TryOrder Delivery Core Intake"

    @staticmethod
    def _discount_label(label):
        text = str(label or "").strip() or "Discount"
        known = {
            "discount": "Order Discount", "order discount": "Order Discount",
            "coupon": "Coupon Discount", "coupon discount": "Coupon Discount",
            "points": "Loyalty Points Discount", "loyalty": "Loyalty Points Discount",
            "loyalty points": "Loyalty Points Discount",
            "loyalty points discount": "Loyalty Points Discount",
        }
        return known.get(text.casefold(), text if text.casefold().endswith("discount") else "%s Discount" % text)

    @api.model
    def _authorized_connections(self, api_key):
        if not isinstance(api_key, str) or not api_key:
            raise TryOrderRequestError("Unauthorized", 401)
        Connection = self.env["bs.delivery.connection"].sudo()
        connections = Connection.search([
            ("provider_id.code", "=", "tryorder"),
            ("state", "in", ("connected", "draft")),
            ("active", "=", True),
        ])
        authorized = connections.filtered(
            lambda connection: (
                (
                    connection.state == "connected"
                    and (
                        connection.environment != "production"
                        or connection.provider_id.implementation_state == "ready"
                    )
                )
                or (
                    connection.state == "draft"
                    and connection.environment == "test"
                    and connection.allow_draft_api_testing
                )
            )
            and bool(connection.webhook_secret)
            and secrets.compare_digest(api_key, connection.webhook_secret)
        )
        if not authorized:
            raise TryOrderRequestError("Unauthorized", 401)
        return authorized

    @api.model
    def _authorized_store(self, api_key, branch_id):
        authorized = self._authorized_connections(api_key)
        try:
            branch_id = int(branch_id)
        except (TypeError, ValueError) as error:
            raise TryOrderRequestError("Invalid branch_id") from error
        stores = self.env["bs.delivery.store.binding"].sudo().search([
            ("connection_id", "in", authorized.ids),
            ("pos_config_id", "=", branch_id),
            ("active", "=", True),
        ])
        if not stores:
            raise TryOrderRequestError("Branch is not linked to this connection", 403)
        if len(stores) != 1:
            raise TryOrderRequestError("TryOrder branch mapping is ambiguous", 409)
        return stores

    @staticmethod
    def _amount(value, label, *, positive=False):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise TryOrderRequestError("%s must be a number" % label)
        try:
            amount = Decimal(str(value))
        except InvalidOperation as error:
            raise TryOrderRequestError("%s must be a number" % label) from error
        if not amount.is_finite() or amount < 0 or (positive and amount == 0):
            raise TryOrderRequestError("%s must be non-negative" % label)
        return amount

    def _force_send(self):
        """A manager's "Force Send to Branch" from the delivery log."""
        return bool(self.env.context.get("bs_delivery_force_send"))

    @api.model
    def _product(self, store, item):
        reference = item.get("product_id")
        if isinstance(reference, bool) or not str(reference or "").isdecimal():
            raise TryOrderRequestError("Unknown product_id")
        template = self.env["product.template"].sudo().browse(int(reference)).exists()
        if not template or len(template.product_variant_ids) != 1:
            raise TryOrderRequestError("Unknown or ambiguous product_id")
        product = template.product_variant_id
        config = store.pos_config_id
        allowed_categories = self.env["pos.category"].sudo().search([
            ("id", "child_of", config.iface_available_categ_ids.ids),
        ]) if config.limit_categories else self.env["pos.category"]
        if product.company_id and product.company_id != store.company_id:
            raise TryOrderRequestError("Product is unavailable in this branch")
        if (not product.active or not product.sale_ok or not product.available_in_pos
                or (config.limit_categories
                    and not (product.pos_categ_ids & allowed_categories))):
            if not self._force_send():
                raise TryOrderRequestError("Product is unavailable in this branch")
            _logger.warning(
                "TryOrder force send: product %s is closed in branch %s",
                product.display_name, config.display_name,
            )
        return product

    @api.model
    def _bind_product(self, store, external_id, product):
        Binding = self.env["bs.delivery.product.binding"].sudo().with_company(store.company_id)
        binding = Binding.with_context(active_test=False).search([
            ("connection_id", "=", store.connection_id.id),
            ("store_binding_id", "=", store.id),
            ("external_product_id", "=", external_id),
        ], limit=1)
        if binding:
            if not binding.active or binding.product_id != product:
                raise TryOrderRequestError("Product mapping conflicts with this branch", 409)
        else:
            Binding.create({
                "connection_id": store.connection_id.id,
                "store_binding_id": store.id,
                "external_product_id": external_id,
                "product_id": product.id,
            })

    @staticmethod
    def _parse_modifier_reference(reference):
        """Return ``(option_id, group_line_id or None)`` for a POS modifier ID.

        The catalogue publishes ``posmod-<option>-<group line>`` so each product
        has its own modifier; ``posmod-<option>`` is still accepted for orders
        placed against an older catalogue.
        """
        reference = str(reference or "")
        parts = reference[7:].split("-") if reference.startswith("posmod-") else []
        if not parts or len(parts) > 2 or not all(part.isdecimal() for part in parts):
            raise TryOrderRequestError("Modifier option ID is invalid")
        return int(parts[0]), (int(parts[1]) if len(parts) == 2 else None)

    @staticmethod
    def _canonical_modifier_reference(option):
        """Core links ``posmod-<option>`` POS lines to that exact option."""
        return "posmod-%s" % option.id

    @api.model
    def _modifier_option(self, store, parent, reference):
        option_id, line_id = self._parse_modifier_reference(reference)
        option = self.env["pos.modifier.option"].sudo().browse(option_id).exists()
        if not option:
            raise TryOrderRequestError("Modifier option does not exist")
        if not option.active and not self._force_send():
            raise TryOrderRequestError("Modifier option is inactive")
        if not option.group_id.active and not self._force_send():
            raise TryOrderRequestError("Modifier group is inactive")
        if not parent.product_tmpl_id.allow_modifiers:
            raise TryOrderRequestError("Product does not allow modifiers")
        group_line = parent.product_tmpl_id.modifier_group_line_ids.filtered(
            lambda line: line.modifier_group_id == option.group_id
            and (line_id is None or line.id == line_id)
        )[:1]
        if not group_line:
            raise TryOrderRequestError("Modifier group is not linked to this product")
        if option.group_id.company_id != store.company_id:
            raise TryOrderRequestError("Modifier group belongs to a different company")
        effective = group_line._effective_option(option)
        if ("available" in effective._fields and not effective.available
                and not self._force_send()):
            raise TryOrderRequestError("Modifier option is disabled for this product")
        return option, effective

    @staticmethod
    def _per_parent_modifier_quantity(option_quantity, item_quantity, max_per_item):
        """Return the modifier quantity for one unit of its parent item.

        The contract is "per parent item", but TryOrder also sends the total
        for all units: 2 burgers "Without pickles" arrive as qty 2, i.e. 1
        per burger.  When the per-item reading exceeds the option's limit and
        the quantity divides evenly by the item quantity, it is a total.
        """
        if (item_quantity > 1 and max_per_item
                and option_quantity > Decimal(str(max_per_item))
                and option_quantity % item_quantity == 0):
            return option_quantity / item_quantity
        return option_quantity

    @staticmethod
    def _modifier_request_error(error, product, reference, item_index, modifier_index):
        return TryOrderRequestError(
            "product %s, modifier %s at order.items[%s].modifiers[%s]: %s" % (
                product.product_tmpl_id.id, reference or "(missing)",
                item_index, modifier_index, error,
            ),
            error.status,
        )

    @api.model
    def _option_product(self, store, parent, reference):
        reference = str(reference or "")
        if reference.startswith("ptav-") and reference[5:].isdecimal():
            ptav = self.env["product.template.attribute.value"].sudo().browse(
                int(reference[5:])
            ).exists()
            if not ptav:
                raise TryOrderRequestError("Attribute option does not exist")
            if ptav not in parent.product_tmpl_id.attribute_line_ids.product_template_value_ids:
                raise TryOrderRequestError("Attribute option is not linked to this product")
            taxes = parent.taxes_id.filtered(
                lambda tax: not tax.company_id or tax.company_id == store.company_id
            )
            code = "TRYORDER-PTAV-%s-%s" % (store.company_id.id, ptav.id)
            Product = self.env["product.product"].sudo().with_company(store.company_id)
            product = Product.search([
                ("default_code", "=", code),
                ("company_id", "=", store.company_id.id),
            ], limit=1)
            if not product:
                product = Product.create({
                    "name": ptav.name,
                    "default_code": code,
                    "type": "service",
                    "company_id": store.company_id.id,
                    "responsible_id": False,
                    "sale_ok": True,
                    "purchase_ok": False,
                    "available_in_pos": False,
                    "list_price": 0.0,
                    "taxes_id": [(6, 0, taxes.ids)],
                })
            elif product.taxes_id != taxes:
                raise TryOrderRequestError("Modifier tax configuration differs from its parent product")
            return product
        if reference.startswith("posmod-"):
            option, effective = self._modifier_option(store, parent, reference)
            if option.option_type == "product" and effective.product_id:
                return effective.product_id
            raise TryOrderRequestError("Modifier option has no product configured")
        if reference.startswith("comboitem-") and reference[10:].isdecimal():
            combo_item = self.env["product.combo.item"].sudo().browse(int(reference[10:])).exists()
            if (combo_item and combo_item.combo_id in parent.product_tmpl_id.combo_ids
                    and (not combo_item.combo_id.company_id
                         or combo_item.combo_id.company_id == store.company_id)
                    and combo_item.product_id and combo_item.product_id.active
                    and combo_item.product_id.sale_ok
                    and (not combo_item.product_id.company_id
                         or combo_item.product_id.company_id == store.company_id)):
                return combo_item.product_id
        raise TryOrderRequestError("Combo option is unavailable for this product")

    @api.model
    def _priced_line(self, store, product, *, quantity, price, external_line_id,
                     external_product_id, parent_external_line_id=None, note=None):
        currency = store.pos_config_id.currency_id
        taxes = product.taxes_id.filtered(
            lambda tax: not tax.company_id or tax.company_id == store.company_id
        )
        taxes = store.fiscal_position_id.map_tax(taxes) if store.fiscal_position_id else taxes
        computed = taxes.compute_all(
            float(price), currency, float(quantity), product=product,
        ) if taxes else {
            "total_excluded": float(quantity * price),
            "total_included": float(quantity * price),
        }
        net = Decimal(str(currency.round(computed["total_excluded"])))
        total = Decimal(str(currency.round(computed["total_included"])))
        values = {
            "external_line_id": external_line_id,
            "external_product_id": external_product_id,
            "name": product.display_name,
            "quantity": float(quantity),
            "price_unit": float(round(net / quantity, 12)),
            "discount_amount": 0.0,
            "vat_amount": float(total - net),
            "price_subtotal": float(net),
        }
        if parent_external_line_id:
            values["parent_external_line_id"] = parent_external_line_id
        if note:
            values["note"] = str(note)
        return values, net, total - net

    @api.model
    def _allocate_combo_price(self, store, parent, children):
        """Put the combo base price on its POS choice lines before Core intake."""
        currency = store.pos_config_id.currency_id
        parent_gross = (
            Decimal(str(parent["price_subtotal"]))
            + Decimal(str(parent["vat_amount"]))
        )
        weights = [
            Decimal(str(line["price_subtotal"])) + Decimal(str(line["vat_amount"]))
            for line, _product in children
        ]
        total_weights = sum(weights)
        remaining = parent_gross
        parent.update({"price_unit": 0.0, "price_subtotal": 0.0, "vat_amount": 0.0})
        for index, (line, product) in enumerate(children):
            if index == len(children) - 1:
                portion = remaining
            else:
                weight = weights[index]
                share = (
                    parent_gross * weight / total_weights
                    if total_weights else parent_gross / len(children)
                )
                portion = Decimal(str(currency.round(float(share))))
                remaining -= portion
            gross = weights[index] + portion
            taxes = product.taxes_id.filtered(
                lambda tax: not tax.company_id or tax.company_id == store.company_id
            )
            if store.fiscal_position_id:
                taxes = store.fiscal_position_id.map_tax(taxes)
            if taxes:
                details = taxes._get_tax_details(
                    float(gross) / line["quantity"], line["quantity"],
                    precision_rounding=currency.rounding,
                    rounding_method="round_globally", product=product,
                    special_mode="total_included",
                )
                net = Decimal(str(currency.round(details["total_excluded"])))
            else:
                net = gross
            line.update({
                "price_unit": float(round(net / Decimal(str(line["quantity"])), 12)),
                "price_subtotal": float(net),
                "vat_amount": float(gross - net),
            })

    @api.model
    def _normalize_order(self, order, store):
        if not isinstance(order, dict):
            raise TryOrderRequestError("order must be an object")
        external_id = str(order.get("provider_order_id") or "").strip()
        if not external_id:
            raise TryOrderRequestError("provider_order_id is required")
        items = order.get("items")
        if not isinstance(items, list) or not items:
            raise TryOrderRequestError("order.items is required")
        breakdown = order.get("discounts") or []
        if not isinstance(breakdown, list):
            raise TryOrderRequestError("order.discounts must be an array")
        wallet_in_breakdown = Decimal(0)
        untaxed_discounts = []
        for component in breakdown:
            if not isinstance(component, dict):
                raise TryOrderRequestError("Each discount must be an object")
            amount = self._amount(component.get("amount", 0), "discount.amount")
            label = str(component.get("label") or "").lower()
            if "wallet" in label or "محفظ" in label:
                wallet_in_breakdown += amount
            elif amount:
                untaxed_discounts.append({
                    "label": self._discount_label(component.get("label")),
                    "amount": float(amount),
                })
        if not breakdown:
            aggregate_discount = self._amount(order.get("discount", 0), "order.discount")
            if aggregate_discount:
                untaxed_discounts.append({
                    "label": self._discount_label("Discount"),
                    "amount": float(aggregate_discount),
                })
        wallet_amount = max(
            self._amount(order.get("wallet_amount", 0), "order.wallet_amount"),
            wallet_in_breakdown,
        )
        delivery_fee = self._amount(order.get("delivery_fee", 0), "order.delivery_fee")
        if delivery_fee:
            fee_product = store.delivery_fee_product_id
            if not fee_product:
                raise TryOrderRequestError("Configure a delivery fee product on this branch")
            fee_taxes = fee_product.taxes_id.filtered(
                lambda tax: not tax.company_id or tax.company_id == store.company_id
            )
            if store.fiscal_position_id:
                fee_taxes = store.fiscal_position_id.map_tax(fee_taxes)
            if fee_taxes:
                fee_totals = fee_taxes.compute_all(
                    float(delivery_fee), store.pos_config_id.currency_id, product=fee_product
                )
                if store.pos_config_id.currency_id.compare_amounts(
                    fee_totals["total_included"], fee_totals["total_excluded"]
                ):
                    raise TryOrderRequestError("TryOrder delivery fee product must be untaxed")
        currency = store.pos_config_id.currency_id
        lines = []
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                raise TryOrderRequestError("Each item must be an object")
            product = self._product(store, item)
            quantity = self._amount(item.get("qty"), "item.qty", positive=True)
            price = self._amount(item.get("price"), "item.price")
            reference = str(product.product_tmpl_id.id)
            self._bind_product(store, reference, product)
            parent_id = "item-%s" % index
            line, _net, _tax = self._priced_line(
                store, product, quantity=quantity, price=price,
                external_line_id=parent_id, external_product_id=reference,
                note=item.get("note"),
            )
            lines.append(line)
            modifiers = item.get("modifiers") or []
            if not isinstance(modifiers, list):
                raise TryOrderRequestError("item.modifiers must be an array")
            selected_options = {}
            selected_combos = {}
            combo_children = []
            for option_index, modifier in enumerate(modifiers, 1):
                if not isinstance(modifier, dict):
                    raise TryOrderRequestError("Each modifier must be an object")
                option_reference = str(modifier.get("id") or "")
                try:
                    option_quantity = self._amount(
                        modifier.get("qty", 1), "modifier.qty", positive=True,
                    )
                    option_price = self._amount(modifier.get("price"), "modifier.price")
                except TryOrderRequestError as error:
                    raise self._modifier_request_error(
                        error, product, option_reference, index - 1, option_index - 1,
                    ) from error
                line_reference = option_reference
                if option_reference.startswith("posmod-"):
                    try:
                        option, effective = self._modifier_option(
                            store, product, option_reference,
                        )
                    except TryOrderRequestError as error:
                        raise self._modifier_request_error(
                            error, product, option_reference, index - 1, option_index - 1,
                        ) from error
                    option_quantity = self._per_parent_modifier_quantity(
                        option_quantity, quantity, effective.max_qty,
                    )
                    selected_options[option.id] = (
                        selected_options.get(option.id, Decimal(0)) + option_quantity
                    )
                    if selected_options[option.id] > effective.max_qty:
                        # The customer paid for it on the platform: prepare it
                        # as ordered and leave the menu rule for review.
                        _logger.warning(
                            "TryOrder order accepted outside modifier limits: %s",
                            self._modifier_request_error(
                                TryOrderRequestError("Modifier option quantity exceeds its limit"),
                                product, option_reference, index - 1, option_index - 1,
                            ),
                        )
                    line_reference = self._canonical_modifier_reference(option)
                    if option.option_type == "instruction":
                        if option_price:
                            raise self._modifier_request_error(
                                TryOrderRequestError("Instruction modifiers cannot have a price"),
                                product, option_reference, index - 1, option_index - 1,
                            )
                        lines.append({
                            "external_line_id": "%s-option-%s" % (parent_id, option_index),
                            "external_product_id": "posinstr-%s" % option.id,
                            "name": effective.name,
                            "quantity": float(quantity * option_quantity),
                            "price_unit": 0.0,
                            "discount_amount": 0.0,
                            "vat_amount": 0.0,
                            "price_subtotal": 0.0,
                            "parent_external_line_id": parent_id,
                            **({"note": str(modifier["note"])} if modifier.get("note") else {}),
                        })
                        continue
                try:
                    option_product = self._option_product(store, product, option_reference)
                except TryOrderRequestError as error:
                    raise self._modifier_request_error(
                        error, product, option_reference, index - 1, option_index - 1,
                    ) from error
                if option_reference.startswith("comboitem-"):
                    combo_item = self.env["product.combo.item"].sudo().browse(
                        int(option_reference[10:])
                    )
                    selected_combos[combo_item.id] = (
                        selected_combos.get(combo_item.id, Decimal(0)) + option_quantity
                    )
                    if (combo_item.qty_max and selected_combos[combo_item.id] > combo_item.qty_max
                            and not self._force_send()):
                        raise self._modifier_request_error(
                            TryOrderRequestError("Combo choice quantity exceeds its limit"),
                            product, option_reference, index - 1, option_index - 1,
                        )
                self._bind_product(store, line_reference, option_product)
                child, _child_net, _child_tax = self._priced_line(
                    store, option_product,
                    quantity=quantity * option_quantity,
                    price=option_price,
                    external_line_id="%s-option-%s" % (parent_id, option_index),
                    external_product_id=line_reference,
                    parent_external_line_id=parent_id,
                    note=modifier.get("note"),
                )
                lines.append(child)
                if option_reference.startswith("comboitem-"):
                    combo_children.append((child, option_product))
            if product.product_tmpl_id.allow_modifiers:
                for group_line in product.product_tmpl_id.modifier_group_line_ids:
                    group = group_line.modifier_group_id
                    if not group.active or group.company_id != store.company_id:
                        continue
                    selected = sum(
                        selected_options.get(option.id, Decimal(0))
                        for option in group.option_ids
                    )
                    if selected < group.min_qty or selected > group.max_qty:
                        _logger.warning(
                            "TryOrder order accepted outside modifier group limits: "
                            "product %s, group %s (%s-%s), selected %s",
                            product.product_tmpl_id.id, group.display_name,
                            group.min_qty, group.max_qty, selected,
                        )
            for combo in product.product_tmpl_id.combo_ids:
                selected = sum(
                    selected_combos.get(choice.id, Decimal(0))
                    for choice in combo.combo_item_ids
                )
                if ((selected < combo.qty_min or selected > combo.qty_max)
                        and not self._force_send()):
                    raise TryOrderRequestError("Combo group quantity is outside its limits")
            if product.type == "combo" and combo_children:
                self._allocate_combo_price(store, line, combo_children)
        subtotal = sum(
            (Decimal(str(line["price_subtotal"])) for line in lines), Decimal(0),
        )
        vat = sum((Decimal(str(line["vat_amount"])) for line in lines), Decimal(0))
        discount_total = sum(
            (Decimal(str(component["amount"])) for component in untaxed_discounts),
            Decimal(0),
        )
        amount_total = subtotal + vat + delivery_fee - discount_total
        if amount_total < 0:
            raise TryOrderRequestError("Order discount exceeds the order total")
        if wallet_amount > amount_total:
            raise TryOrderRequestError("Wallet amount exceeds the order total")
        if order.get("total") is not None:
            declared = self._amount(order["total"], "order.total")
            if declared != amount_total:
                if not self._force_send():
                    raise TryOrderRequestError("TryOrder total differs from product and tax amounts")
                _logger.warning(
                    "TryOrder force send: order total %s replaced by Odoo's %s",
                    declared, amount_total,
                )
        fulfillment = {
            "delivery": "delivery", "pickup": "pickup", "pick-up": "pickup",
            "takeaway": "pickup", "drive_thru": "pickup", "drivethru": "pickup",
            "dine_in": "dine_in", "dine-in": "dine_in", "dinein": "dine_in",
        }.get(str(order.get("order_type") or "delivery").lower())
        if not fulfillment:
            raise TryOrderRequestError("Unsupported order_type")
        customer = order.get("customer") or {}
        if not isinstance(customer, dict):
            raise TryOrderRequestError("customer must be an object")
        customer = dict(customer)
        if order.get("note"):
            customer["note"] = "\n".join(filter(None, [
                str(customer.get("note") or ""), str(order["note"]),
            ]))
        payment_type = str(order.get("payment_type") or "cash").strip().lower()
        if payment_type in {"offline", "cash", "cod", "cash_on_delivery"}:
            payment_code = "cash"
            collector = "restaurant"
            collection_status = "expected"
        elif payment_type in {"online", "card", "visa", "credit"}:
            payment_code = "card"
            collector = "aggregator"
            collection_status = "collected"
        else:
            raise TryOrderRequestError("Unsupported payment_type")
        payments = []
        def append_payment(code, amount, collected_by, status, reference=None):
            if not amount:
                return
            try:
                self.env["bs.delivery.payment.service"].sudo()._resolve_payment_method(
                    store.connection_id, store,
                    external_code=code,
                    external_source="tryorder",
                    collected_by=collected_by,
                )
            except DeliveryBusinessError as error:
                raise TryOrderRequestError(str(error)) from error
            payment = {
                "external_payment_code": code,
                "external_source": "tryorder",
                "amount": float(amount),
                "currency": currency.name,
                "collected_by": collected_by,
                "collection_status": status,
            }
            if status == "collected":
                payment["external_payment_reference"] = str(reference)
            payments.append(payment)
        append_payment(
            "wallet", wallet_amount, "aggregator", "collected",
            order.get("wallet_payment_reference") or "%s:wallet" % external_id,
        )
        append_payment(
            payment_code, amount_total - wallet_amount, collector,
            collection_status,
            order.get("payment_reference") or "%s:card" % external_id,
        )
        return {
            "contract_version": 1,
            "external_order_id": external_id,
            "external_source": "tryorder",
            "currency": currency.name,
            "fulfillment_type": fulfillment,
            "price_basis": "tax_excluded",
            "customer": {
                key: str(value or "") for key, value in customer.items()
                if key in {"name", "phone", "email", "address", "note"}
            },
            "lines": lines,
            **({"untaxed_discounts": untaxed_discounts} if untaxed_discounts else {}),
            "totals": {
                "items_subtotal": float(subtotal),
                "discount_amount": float(discount_total),
                "fee_amount": float(delivery_fee),
                "vat_amount": float(vat),
                "rounding_amount": 0.0,
                "amount_total": float(amount_total),
            },
            "payments": payments,
            "fees": ([{"kind": "delivery", "amount": float(delivery_fee),
                       "vat_amount": 0.0}] if delivery_fee else []),
        }

    @api.model
    def receive_order(self, order, api_key):
        store = self._authorized_store(api_key, order.get("branch_id"))
        return self._receive_order_for_store(order, store)

    def _receive_order_for_store(self, order, store):
        """Process an order already scoped to an authenticated TryOrder store."""
        connection = store.connection_id.with_company(store.company_id)
        with self.env.cr.savepoint():
            normalized = self._normalize_order(order, store)
            serialized = json.dumps(order, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            key = hashlib.sha256((
                "tryorder:order:%s:%s:%s" % (
                    connection.id, normalized["external_order_id"], digest,
                )
            ).encode()).hexdigest()
            event, _created = self.env["bs.delivery.webhook.event"].sudo()._get_or_create_event(
                connection, {
                    "connection_id": connection.id,
                    "store_binding_id": store.id,
                    "signature_verified": True,
                    "event_type": "order.created",
                    "external_id": normalized["external_order_id"],
                    "idempotency_key": key,
                    "payload_digest": digest,
                    "payload_size": len(serialized.encode("utf-8")),
                    "raw_payload": serialized,
                    "payload_preview": serialize_masked_payload(serialized),
                    "state": "processing",
                },
            )
            delivery = self.env["bs.delivery.order.service"].sudo().with_company(
                store.company_id
            ).with_context(
                bs_tryorder_draft_test_connection_id=connection.id,
            )._receive_order(connection, store, normalized, event=event)
            event.write({
                "state": "done", "processed_at": fields.Datetime.now(),
                "binding_id": delivery.binding_id.id,
            })
            return delivery

    @api.model
    def update_status(self, order_id, payload, api_key):
        connections = self._authorized_connections(api_key)
        return self._update_status_for_connections(order_id, payload, connections)

    def _update_status_for_connections(self, order_id, payload, connections):
        """Apply a status update scoped to the original connection."""
        delivery = self.env["bs.delivery.order"].sudo().search([
            ("pos_order_id", "=", order_id),
            ("connection_id", "in", connections.ids),
            ("contract_version", "=", 1),
            ("external_source", "=", "tryorder"),
        ], limit=1)
        pending = self.env["bs.delivery.order"].sudo().search([
            ("id", "=", order_id),
            ("pos_order_id", "=", False),
            ("connection_id", "in", connections.ids),
            ("contract_version", "=", 1),
            ("external_source", "=", "tryorder"),
        ], limit=1)
        if delivery and pending:
            raise TryOrderRequestError("Ambiguous order_id", 409)
        if not delivery:
            # Pending Core orders created before POS acceptance still expose
            # their internal ID. Keep their status URL working until they are
            # assigned a POS order. Only connection-scoped records are eligible.
            delivery = pending
        if not delivery or not delivery.store_binding_id:
            raise TryOrderRequestError("Order not found", 404)
        if not isinstance(payload, dict):
            raise TryOrderRequestError("Status body must be an object")
        status = str(payload.get("status") or "").strip().lower()
        state = {
            "pending": "received", "confirmed": "accepted",
            "accepted": "accepted", "preparing": "preparing",
            "ready": "ready", "picked_up": "picked_up",
            "completed": "delivered", "delivered": "delivered",
            "rejected": "rejected", "canceled": "cancelled",
            "cancelled": "cancelled",
        }.get(status)
        if not state:
            raise TryOrderRequestError("Unsupported status")
        store = delivery.store_binding_id
        connection = delivery.connection_id.with_company(store.company_id)
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        key = hashlib.sha256((
            "tryorder:status:%s:%s:%s" % (connection.id, delivery.external_order_id, digest)
        ).encode()).hexdigest()
        with self.env.cr.savepoint():
            event, _created = self.env["bs.delivery.webhook.event"].sudo()._get_or_create_event(
                connection, {
                    "connection_id": connection.id,
                    "store_binding_id": store.id,
                    "signature_verified": True,
                    "event_type": "order.updated",
                    "external_id": delivery.external_order_id,
                    "idempotency_key": key,
                    "payload_digest": digest,
                    "payload_size": len(serialized.encode("utf-8")),
                    "raw_payload": serialized,
                    "payload_preview": serialize_masked_payload(serialized),
                    "state": "processing",
                },
            )
            delivery = self.env["bs.delivery.order.service"].sudo().with_company(
                store.company_id
            ).with_context(
                bs_tryorder_draft_test_connection_id=connection.id,
            )._apply_provider_update(
                connection, store, delivery.external_order_id, {
                    "contract_version": 1,
                    "state": state,
                    "occurred_at": payload.get("occurred_at") or event.create_date.replace(
                        tzinfo=timezone.utc
                    ).isoformat(),
                    "external_status": status,
                }, event=event,
            )
            event.write({
                "state": "done", "processed_at": fields.Datetime.now(),
                "binding_id": delivery.binding_id.id,
            })
            return delivery


class TryOrderDeliverySyncLog(models.Model):
    _inherit = "bs.delivery.sync.log"

    @api.depends("status", "operation", "direction", "request_method", "request_url")
    def _compute_can_force_send(self):
        super()._compute_can_force_send()
        for log in self:
            if (log.status == "failed" and log.operation == "tryorder:create_order"
                    and log.direction == "incoming" and log.request_method == "POST"
                    and log.request_url == "/order_connector/orders"):
                log.can_force_send = True

    def _tryorder_existing_order(self):
        self.ensure_one()
        try:
            payload = json.loads(self.request_payload or "")
        except (TypeError, ValueError):
            return self.env["bs.delivery.order"]
        order = payload.get("order") if isinstance(payload, dict) else None
        reference = str((order or {}).get("provider_order_id") or "").strip() \
            if isinstance(order, dict) else ""
        if not reference:
            return self.env["bs.delivery.order"]
        return self.env["bs.delivery.order"].sudo().search([
            ("connection_id", "=", self.connection_id.id),
            ("external_order_id", "=", reference),
        ], limit=1)

    def _force_send_existing_order(self):
        if self.operation == "tryorder:create_order":
            return self._tryorder_existing_order()
        return super()._force_send_existing_order()

    def _force_send_unlinked_failure(self):
        if self.operation != "tryorder:create_order":
            return super()._force_send_unlinked_failure()
        return self._retry_unlinked_failure()

    def _retry_unlinked_failure(self):
        self.ensure_one()
        if self.operation not in ("tryorder:create_order", "tryorder:update_status"):
            return super()._retry_unlinked_failure()
        connection = self.connection_id
        if (self.direction != "incoming"
                or connection.provider_id.code != "tryorder"
                or not connection.active
                or not (connection.state == "connected"
                        or (connection.state == "draft" and connection.environment == "test"
                            and connection.allow_draft_api_testing))):
            raise UserError("This TryOrder log is not a verified order request.")
        if (not self.request_payload or "...[truncated]" in self.request_payload
                or "********" in self.request_payload):
            raise UserError("The saved TryOrder request is incomplete; ask TryOrder to resend it.")
        try:
            payload = json.loads(self.request_payload)
        except (TypeError, ValueError) as error:
            raise UserError("The saved TryOrder request is not valid JSON.") from error
        intake = self.env["bs.delivery.tryorder.intake"]
        if self.operation == "tryorder:create_order":
            if self.request_method != "POST" or self.request_url != "/order_connector/orders":
                raise UserError("This log is not a TryOrder create request.")
            order_payload = payload.get("order") if isinstance(payload, dict) else None
            if not isinstance(order_payload, dict):
                raise UserError("The saved TryOrder order is incomplete.")
            try:
                branch_id = int(order_payload.get("branch_id"))
            except (TypeError, ValueError) as error:
                raise UserError("The TryOrder branch is invalid.") from error
            stores = self.env["bs.delivery.store.binding"].sudo().search([
                ("connection_id", "=", self.connection_id.id),
                ("pos_config_id", "=", branch_id),
                ("active", "=", True),
            ])
            if len(stores) != 1:
                raise UserError("The TryOrder branch mapping is unavailable or ambiguous.")
            existing = self._tryorder_existing_order()
            if (existing and existing.store_binding_id == stores
                    and existing.pos_processing_state not in ("blocked", "waiting_session")):
                # Another log of the same order was already retried.
                return {"delivery_order_id": existing.id,
                        "pos_order_id": existing.pos_order_id.id,
                        "already_processed": True}
            order = intake._receive_order_for_store(order_payload, stores.ensure_one())
        else:
            match = re.fullmatch(r"/order_connector/orders/(\d+)/status", self.request_url or "")
            if self.request_method not in ("PUT", "POST") or not match:
                raise UserError("This log is not a TryOrder status request.")
            order = intake._update_status_for_connections(
                int(match.group(1)), payload, self.connection_id,
            )
        order = order.with_user(self.env.user)
        if (order.state == "received" and order.pos_processing_state != "blocked"
                and order.connection_id.delivery_accept_action == "accept"):
            order._apply_initial_acceptance_policy()
        if order.pos_processing_state in ("blocked", "waiting_session"):
            order.action_retry_pos_processing()
        if order.pos_processing_state == "blocked":
            raise UserError("TryOrder POS processing still needs review.")
        if self.env.context.get("bs_delivery_force_send") and (
                order.pos_order_id or order.state == "received"):
            # Cashier and kitchen only need the POS order (or the order on the
            # POS delivery board); payment and invoice stay visible for review.
            return {"delivery_order_id": order.id, "pos_order_id": order.pos_order_id.id}
        if order.state in ("accepted", "preparing", "ready", "picked_up"):
            invoice = order.pos_order_id.account_move
            if (order.pos_processing_state != "done" or not order.pos_order_id
                    or order.pos_order_id.state != "done" or not invoice
                    or invoice.state != "posted"
                    or not order.currency_id.is_zero(invoice.amount_residual)):
                raise UserError("TryOrder POS payment and invoice are not complete yet.")
        return {"delivery_order_id": order.id, "pos_order_id": order.pos_order_id.id}
