"""Translate the Blend POS order API into the authenticated Core v1 contract."""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.bs_pos_delivery_core.exceptions import (
    DeliveryAuthenticationError,
    DeliveryBusinessError,
    DeliveryWebhookConflictError,
)
from odoo.addons.bs_pos_delivery_core.tools import serialize_masked_payload

from ..services.exceptions import BlendAPIError
from ..services.pos_api import BlendPOSAPIService


_logger = logging.getLogger(__name__)


class BlendOrderIntake(models.AbstractModel):
    _name = "bs.delivery.blend.intake"
    _description = "Blend Core Order Intake"

    _ORDER_FIELDS = frozenset({
        "branch_id", "brand_id", "partner_reference", "aggregator_order_id",
        "aggregator", "order_type", "status", "subtotal", "tax", "total",
        "customer", "note", "products", "schedule_at", "payment_type",
        "event_id",
    })
    _PAYMENT_TYPES = {
        "cash": "cash", "cod": "cash", "1": "cash",
        "card": "card", "visa": "card", "credit": "card", "credit/visa": "card",
        "credit_visa": "card", "0": "card",
    }
    _FULFILLMENT = {
        "delivery": "delivery", "pick-up": "pickup", "pickup": "pickup",
        "takeaway": "pickup", "dine-in": "dine_in", "dine_in": "dine_in",
    }
    _STATUS_FIELDS = frozenset({"branch_id", "brand_id", "order_id", "status", "event_id"})
    _STATUS_STATES = {
        "PENDING": "received", "SCHEDULED": "received",
        "ACCEPTED": "accepted", "ASSIGNED": "accepted",
        "PREPARING": "preparing", "PREPARED": "ready", "READY_TO_PICK_UP": "ready",
        "AT_PICK_UP": "picked_up", "ON_THE_WAY": "picked_up",
        "DELIVERED": "delivered", "COMPLETED": "delivered",
        "REJECTED": "rejected", "CANCELED": "cancelled",
        "CANCELLED": "cancelled", "TIME_OUT": "cancelled",
    }
    _PROGRESSION = ("received", "accepted", "preparing", "ready", "picked_up", "delivered")

    @staticmethod
    def _text(value, label):
        if not isinstance(value, str) or not value.strip():
            raise BlendAPIError("%s must be a non-empty string." % label, 422)
        return value.strip()

    @staticmethod
    def _money(value, currency, label):
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise BlendAPIError("%s must be a monetary amount." % label, 422)
        try:
            amount = Decimal(str(value))
        except InvalidOperation as error:
            raise BlendAPIError("%s must be a monetary amount." % label, 422) from error
        if (not amount.is_finite() or amount < 0 or amount > Decimal("1e12")
                or Decimal(str(currency.round(float(amount)))) != amount):
            raise BlendAPIError("%s must be a non-negative rounded amount." % label, 422)
        return amount

    @staticmethod
    def _quantity(value, label):
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise BlendAPIError("%s must be a positive number." % label, 422)
        try:
            amount = Decimal(str(value))
        except InvalidOperation as error:
            raise BlendAPIError("%s must be a positive number." % label, 422) from error
        if not amount.is_finite() or amount <= 0 or amount > Decimal("1e6") or amount != amount.to_integral_value():
            raise BlendAPIError("%s must be a positive integer." % label, 422)
        return amount

    def _store(self, payload, authorization):
        branch = self._text(payload.get("branch_id"), "branch_id")
        brand = self._text(payload.get("brand_id"), "brand_id")
        webhook = self.env["bs.delivery.blend.webhook.service"]
        stores = webhook._resolve_stores({"branch_id": branch})
        stores = webhook._authorized_stores(stores, authorization)
        if len(stores) != 1:
            raise BlendAPIError("Blend branch mapping is ambiguous.", 409)
        store = stores.ensure_one()
        if brand != (store.remote_vendor_id or store.connection_id.blend_brand_id or "").strip():
            raise BlendAPIError("Blend brand does not match this branch.", 403)
        return store

    def _blend_modifier_target(self, modifier_id, group_lines):
        """Resolve a per-product Blend modifier ID published by ``/api/menu``.

        Returns ``None`` when the ID is not one of the reserved per-product IDs
        (older numeric or Blend-owned IDs keep the product-based matching),
        ``False`` when it is reserved but no longer offered on this product,
        else ``(group_line, option, effective)``.
        """
        decoded = BlendPOSAPIService.decode_modifier_id(modifier_id)
        if not decoded:
            return None
        kind, local_id = decoded
        if kind == "identity":
            record = self.env["bs.blend.modifier.identity"].sudo().browse(local_id).exists()
        else:
            record = self.env["pos.product.modifier.option.line"].sudo().browse(local_id).exists()
        group_line, option = record.group_line_id, record.option_id
        if (
            not record
            or group_line not in group_lines
            or not option.active
            or option.group_id != group_line.modifier_group_id
        ):
            return False
        effective = group_line._effective_option(option)
        if effective._name == "pos.product.modifier.option.line" and not effective.available:
            return False
        return group_line, option, effective

    def _resolve_item_product(self, binding_model, store, item_id, *, modifier=False):
        try:
            return binding_model.resolve_order_product(
                store.connection_id, store, item_id, modifier=modifier,
            )
        except DeliveryBusinessError as exc:
            if exc.code != "product_not_found":
                raise BlendAPIError(str(exc), 422)
        # Modifier option or combo item resolution
        if modifier:
            if "pos.modifier.option" in self.env:
                option = self.env["pos.modifier.option"].sudo().search([
                    ("blend_external_id", "=", item_id),
                    ("active", "=", True),
                    "|",
                    ("company_id", "=", False),
                    ("company_id", "=", store.company_id.id),
                ], limit=1)
                if not option and item_id.isdecimal():
                    option = self.env["pos.modifier.option"].sudo().browse(int(item_id)).exists()
                    if option and option.company_id and option.company_id != store.company_id:
                        option = self.env["pos.modifier.option"].browse()
                if option and option.product_id:
                    return option.product_id

            if item_id.isdecimal() and "product.combo.item" in self.env:
                combo_item = self.env["product.combo.item"].sudo().browse(int(item_id)).exists()
                if combo_item and combo_item.product_id:
                    return combo_item.product_id
        # Fallback 1: match blend_external_id on product.template
        template = self.env["product.template"].sudo().search([
            ("blend_external_id", "=", item_id),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", store.company_id.id),
        ], limit=1)
        if template:
            product = template.product_variant_id
            if product:
                if not modifier and (
                    not product.available_in_pos
                    or (
                        store.pos_config_id.limit_categories
                        and not (product.pos_categ_ids & store.pos_config_id.iface_available_categ_ids)
                    )
                ):
                    raise BlendAPIError("Product '%s' is not available in POS." % product.display_name, 422)
                return product
        # Fallback 2: if numeric, match Odoo product ID
        if item_id.isdecimal():
            template = self.env["product.template"].sudo().browse(int(item_id)).exists()
            if template and (not template.company_id or template.company_id == store.company_id):
                product = template.product_variant_id
                if product:
                    if not modifier and (
                        not product.available_in_pos
                        or (
                            store.pos_config_id.limit_categories
                            and not (product.pos_categ_ids & store.pos_config_id.iface_available_categ_ids)
                        )
                    ):
                        raise BlendAPIError("Product '%s' is not available in POS." % product.display_name, 422)
                    return product
            product = self.env["product.product"].sudo().browse(int(item_id)).exists()
            if product and (not product.company_id or product.company_id == store.company_id):
                if not modifier and (
                    not product.available_in_pos
                    or (
                        store.pos_config_id.limit_categories
                        and not (product.pos_categ_ids & store.pos_config_id.iface_available_categ_ids)
                    )
                ):
                    raise BlendAPIError("Product '%s' is not available in POS." % product.display_name, 422)
                return product
        raise BlendAPIError("Blend product '%s' was not found." % item_id, 404)

    def _warn_modifier_limit(self, message):
        """Modifier quantity rules guide the POS screen, not platform orders.

        The customer already ordered and paid on the platform, so a selection
        outside Odoo's modifier limits is prepared as ordered instead of
        rejecting the whole order.  The difference is logged for menu review.
        """
        _logger.warning("Blend order accepted outside modifier limits: %s", message)

    def _lines(self, payload, store, *, subtotal, tax, total, resolved=None):
        """Build Core lines; ``resolved`` collects {external_line_id: product_id}.

        Blend resolves products through several fallbacks (Core mapping,
        ``blend_external_id``, modifier options, combo items, Odoo IDs). Core's
        line builder only knows explicit mappings, so the resolved product is
        handed over through ``bs_blend_resolved_products`` context.
        """
        resolved = {} if resolved is None else resolved
        currency = store.pos_config_id.currency_id
        # Manager "Force Send to Branch": Odoo's own products and taxes decide
        # the amounts when Blend's header disagrees, instead of a rejection.
        force = bool(self.env.context.get("bs_blend_force_totals"))
        products = payload.get("products")
        if not isinstance(products, list) or not products:
            raise BlendAPIError("products must be a non-empty array.", 422)
        binding_model = self.env["bs.delivery.product.binding"].sudo().with_company(store.company_id)
        availability = BlendPOSAPIService(self.env, store.pos_config_id)
        entries = []
        unit_modifier_grosses = {}
        for index, item in enumerate(products, 1):
            if not isinstance(item, dict) or set(item) - {"id", "quantity", "total", "note", "modifiers"}:
                raise BlendAPIError("Invalid products[] entry.", 422)
            item_id = self._text(str(item.get("id") or ""), "products[].id")
            item_id = str(int(item_id)) if item_id.isdecimal() else item_id
            qty = self._quantity(item.get("quantity"), "products[].quantity")
            gross = self._money(item.get("total"), currency, "products[].total")
            product = self._resolve_item_product(binding_model, store, item_id, modifier=False)
            parent_key = "item-%s" % index
            resolved[parent_key] = product.id
            parent_entry = len(entries)
            instruction_notes = []
            entries.append((parent_key, None, item_id, product, qty, gross, item.get("note")))
            modifiers = item.get("modifiers", [])
            if modifiers is None:
                modifiers = []
            if not isinstance(modifiers, list):
                raise BlendAPIError("products[].modifiers must be an array.", 422)
            group_lines = product.product_tmpl_id.modifier_group_line_ids.filtered(
                lambda line: line.modifier_group_id.active
                and (not line.modifier_group_id.blend_config_id
                     or line.modifier_group_id.blend_config_id == store.pos_config_id)
                and line.modifier_group_id.blend_menu_active
                and line.modifier_group_id.blend_is_available
            )
            selections = {line.modifier_group_id.id: 0 for line in group_lines}
            for modifier_index, modifier in enumerate(modifiers, 1):
                if not isinstance(modifier, dict) or set(modifier) - {"id", "quantity", "total", "note"}:
                    raise BlendAPIError("Invalid products[].modifiers[] entry.", 422)
                modifier_id = self._text(str(modifier.get("id") or ""), "modifiers[].id")
                modifier_id = str(int(modifier_id)) if modifier_id.isdecimal() else modifier_id
                selection_qty = self._quantity(modifier.get("quantity"), "modifiers[].quantity")
                modifier_qty = selection_qty * qty
                modifier_total = self._money(
                    modifier.get("total"), currency, "modifiers[].total",
                )
                modifier_gross = modifier_total * qty
                target = self._blend_modifier_target(modifier_id, group_lines)
                if target is False:
                    raise BlendAPIError(
                        "Modifier '%s' is not uniquely assigned to this product." % modifier_id,
                        422,
                    )
                if target:
                    group_line, option, effective = target
                    group = group_line.modifier_group_id
                    if option.option_type == "instruction":
                        # Preparation notes ("Without onions") have no product:
                        # they travel as a note on the ordered item.
                        if modifier_total:
                            raise BlendAPIError(
                                "Modifier '%s' is a preparation note and cannot be charged." % modifier_id,
                                422,
                            )
                        if selection_qty > effective.max_qty:
                            self._warn_modifier_limit(
                                "Modifier '%s' exceeds max_per_quantity=%s." % (
                                    modifier_id, effective.max_qty,
                                ),
                            )
                        selections[group.id] += selection_qty
                        instruction_notes.append(effective.name or option.name)
                        continue
                    modifier_product = effective.product_id
                    matches = [(group, option, effective)]
                else:
                    modifier_product = self._resolve_item_product(
                        binding_model, store, modifier_id, modifier=True,
                    )
                    matches = []
                    for group_line in group_lines:
                        for option in group_line.modifier_group_id.option_ids.filtered("active"):
                            effective = group_line._effective_option(option)
                            if (effective.product_id == modifier_product
                                    and (not hasattr(effective, "available") or effective.available)):
                                matches.append((group_line.modifier_group_id, option, effective))
                    identified = [
                        match for match in matches
                        if match[1].blend_external_id == modifier_id
                        or str(match[1].id) == modifier_id
                    ]
                    if identified:
                        matches = identified
                if len(matches) != 1 or not modifier_product:
                    raise BlendAPIError(
                        "Modifier '%s' is not uniquely assigned to this product." % modifier_id,
                        422,
                    )
                group, option, effective = matches[0]
                if (
                    not availability._modifier_product_in_catalog(
                        modifier_product, store.pos_config_id,
                    )
                    or not availability._product_available(
                        modifier_product.product_tmpl_id, store.pos_config_id,
                        modifier=True,
                    )
                ) and not force:
                    raise BlendAPIError(
                        "Modifier '%s' is unavailable." % modifier_id, 409,
                    )
                if selection_qty > effective.max_qty:
                    self._warn_modifier_limit(
                        "Modifier '%s' exceeds max_per_quantity=%s." % (
                            modifier_id, effective.max_qty,
                        ),
                    )
                selections[group.id] += selection_qty
                modifier_key = "item-%s-option-%s" % (index, modifier_index)
                resolved[modifier_key] = modifier_product.id
                unit_modifier_grosses[len(entries)] = modifier_total * modifier_qty
                entries.append((
                    modifier_key, parent_key,
                    # Core links ``posmod-<option>`` to that exact option; the
                    # scoped Blend ID itself has no meaning outside this intake.
                    "posmod-%s" % option.id if target else modifier_id,
                    modifier_product, modifier_qty, modifier_gross,
                    modifier.get("note"),
                ))
            if instruction_notes:
                parent_note = entries[parent_entry][6]
                if parent_note is not None and not isinstance(parent_note, str):
                    raise BlendAPIError("A product note must be text.", 422)
                note = " / ".join(instruction_notes)
                entries[parent_entry] = entries[parent_entry][:6] + (
                    "%s\n%s" % (parent_note, note) if parent_note else note,
                )
            for group_line in group_lines:
                group = group_line.modifier_group_id
                minimum = group.min_qty
                maximum = group.max_qty
                selected = selections[group.id]
                if selected < minimum or selected > maximum:
                    self._warn_modifier_limit(
                        "Modifier group '%s' requires between %s and %s selections, "
                        "the customer chose %s." % (
                            group.display_name, minimum, maximum, selected,
                        ),
                    )
        gross_sum = sum((entry[5] for entry in entries), Decimal(0))
        discount = subtotal + tax - total
        if (discount < 0 or discount > subtotal) and not force:
            raise BlendAPIError("Invalid Blend subtotal, tax and total.", 422)
        if gross_sum not in (total, subtotal + tax):
            unit_gross_sum = gross_sum + sum(
                (unit_gross - entries[index][5]
                 for index, unit_gross in unit_modifier_grosses.items()), Decimal(0)
            )
            if unit_gross_sum in (total, subtotal + tax):
                # Accept unit-priced modifiers only when the order header confirms them.
                for index, unit_gross in unit_modifier_grosses.items():
                    entry = entries[index]
                    entries[index] = entry[:5] + (unit_gross,) + entry[6:]
                gross_sum = unit_gross_sum
            elif not force:
                raise BlendAPIError("Blend product totals do not match the order header.", 422)
        if force and not Decimal(0) <= total <= gross_sum:
            # The paid amount can only be the products, less a discount.
            total = gross_sum
        gross_discount = gross_sum - total
        remaining_gross = gross_discount
        prepared = []
        for index, (key, parent, external_id, product, qty, gross, note) in enumerate(entries):
            share = (
                remaining_gross if index == len(entries) - 1
                else Decimal(str(currency.round(float(gross_discount * gross / gross_sum))))
                if gross_sum else Decimal(0)
            )
            remaining_gross -= share
            final_gross = gross - share
            if final_gross < 0:
                raise BlendAPIError("The Blend discount exceeds a product amount.", 422)
            taxes = product.taxes_id.filtered(
                lambda row: not row.company_id or row.company_id == store.company_id
            )
            effective = store.fiscal_position_id.map_tax(taxes)
            details = effective._get_tax_details(
                float(final_gross / qty), float(qty),
                precision_rounding=currency.rounding,
                rounding_method="round_globally", product=product,
                special_mode="total_included",
            )
            net = Decimal(str(currency.round(details["total_excluded"])))
            vat = final_gross - net
            prepared.append([key, parent, external_id, product, qty, net, vat, note, gross, effective])
        final_net = sum((item[5] for item in prepared), Decimal(0))
        actual_tax = sum((item[6] for item in prepared), Decimal(0))
        accepted_total = total
        # Blend may report no separate tax while product totals already include Odoo VAT.
        included_tax_reported_as_zero = not tax and gross_sum == subtotal and actual_tax > 0
        if (included_tax_reported_as_zero and not gross_discount
                and all(all(tax.price_include for tax in item[9]) for item in prepared)):
            tax_model = self.env["account.tax"].with_company(store.company_id)
            base_lines = [
                tax_model._prepare_base_line_for_taxes_computation(
                    None, currency_id=currency, product_id=item[3], tax_ids=item[9],
                    price_unit=float(item[8] / item[4]), quantity=float(item[4]),
                )
                for item in prepared
            ]
            tax_model._add_tax_details_in_base_lines(base_lines, store.company_id)
            tax_model._round_base_lines_tax_details(base_lines, store.company_id)
            for item, base_line in zip(prepared, base_lines, strict=True):
                details = base_line["tax_details"]
                net = Decimal(str(currency.round(
                    details["total_excluded_currency"]
                    + details["delta_total_excluded_currency"]
                )))
                item[6] = item[8] - net
                item[5] = net
            actual_tax = sum((item[6] for item in prepared), Decimal(0))
            original_unit_prices = [item[5] / item[4] for item in prepared]
            line_discounts = [Decimal(0)] * len(prepared)
            items_subtotal = sum((item[5] for item in prepared), Decimal(0))
            # The global accounting base delta can land on an untaxed product.
            # Its POS price then differs from Blend's gross line amount. Use the
            # actual POS prices and tax calculation before deciding whether the
            # provider and Odoo totals round to the same whole currency unit.
            pos_base_lines = [
                tax_model._prepare_base_line_for_taxes_computation(
                    None, currency_id=currency, product_id=item[3], tax_ids=item[9],
                    price_unit=float(
                        (item[8] if item[9] else item[5]) / item[4]
                    ),
                    quantity=float(item[4]),
                )
                for item in prepared
            ]
            tax_model._add_tax_details_in_base_lines(pos_base_lines, store.company_id)
            tax_model._round_base_lines_tax_details(pos_base_lines, store.company_id)
            pos_totals = tax_model._get_tax_totals_summary(
                base_lines=pos_base_lines, currency=currency, company=store.company_id,
            )
            pos_total = Decimal(str(currency.round(pos_totals["total_amount_currency"])))
            if (pos_total != total
                    and pos_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                    == total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)):
                accepted_total = pos_total
                for item, base_line in zip(prepared, pos_base_lines, strict=True):
                    details = base_line["tax_details"]
                    gross = Decimal(str(currency.round(
                        details["total_included_currency"]
                    )))
                    vat = Decimal(str(currency.round(sum(
                        tax_data["tax_amount_currency"]
                        for tax_data in details["taxes_data"]
                    ))))
                    item[5] = gross - vat
                    item[6] = vat
                actual_tax = sum((item[6] for item in prepared), Decimal(0))
                original_unit_prices = [item[5] / item[4] for item in prepared]
                items_subtotal = sum((item[5] for item in prepared), Decimal(0))
        elif included_tax_reported_as_zero:
            original_unit_prices = [
                Decimal(str(item[9]._get_tax_details(
                    float(item[8] / item[4]), float(item[4]),
                    precision_rounding=currency.rounding,
                    rounding_method="round_globally", product=item[3],
                    special_mode="total_included",
                )["total_excluded"])) / item[4]
                for item in prepared
            ]
            original_nets = [
                Decimal(str(currency.round(float(price * item[4]))))
                for price, item in zip(original_unit_prices, prepared)
            ]
            # Stay between the raw inverse tax price and its currency-rounded
            # counterpart to avoid a cent drifting between POS and its invoice.
            original_unit_prices = [
                (price + original / item[4]) / 2
                for price, original, item in zip(original_unit_prices, original_nets, prepared)
            ]
            line_discounts = [original - item[5] for original, item in zip(original_nets, prepared)]
            discount = sum(line_discounts, Decimal(0))
            items_subtotal = sum(original_nets, Decimal(0))
        else:
            if actual_tax != tax or final_net + discount != subtotal:
                # Blend rounds VAT on the order, Odoo on each line: a few
                # halalas apart with the same total is rounding, not an error.
                tolerance = Decimal(str(store.connection_id.tax_rounding_tolerance or 0))
                rounding_only = (
                    Decimal(0) <= discount <= subtotal
                    and abs(actual_tax - tax) <= tolerance
                    and abs(final_net + discount - subtotal) <= tolerance
                )
                if rounding_only:
                    _logger.info(
                        "Blend tax rounding difference accepted: blend_tax=%s odoo_tax=%s",
                        tax, actual_tax,
                    )
                elif not force:
                    raise BlendAPIError("Blend totals differ from the configured Odoo product taxes.", 422)
                # Odoo's taxes win; restate Blend's discount without tax so the
                # lines, the invoice and the payment all add up to ``total``.
                if not rounding_only:
                    discount = (
                        Decimal(str(currency.round(float(gross_discount * final_net / total))))
                        if gross_discount and total else Decimal(0)
                    )
            items_subtotal = final_net + discount
        remaining_discount = discount
        lines = []
        for index, (key, parent, external_id, product, qty, net, vat, note, _gross, _taxes) in enumerate(prepared):
            if included_tax_reported_as_zero:
                line_discount = line_discounts[index]
                price_unit = original_unit_prices[index]
            else:
                line_discount = (
                    remaining_discount if index == len(prepared) - 1
                    else Decimal(str(currency.round(float(discount * net / final_net))))
                    if final_net else Decimal(0)
                )
                price_unit = (net + line_discount) / qty
            remaining_discount -= line_discount
            values = {
                "external_line_id": key,
                "external_product_id": external_id,
                "name": product.display_name,
                "quantity": float(qty),
                "price_unit": float(round(price_unit, 12)),
                "discount_amount": float(line_discount),
                "vat_amount": float(vat),
                "price_subtotal": float(net),
            }
            if parent:
                values["parent_external_line_id"] = parent
            if note is not None:
                if not isinstance(note, str):
                    raise BlendAPIError("A product note must be text.", 422)
                values["note"] = note
            lines.append(values)
        return (lines, float(items_subtotal), float(discount), float(actual_tax),
                float(accepted_total))

    def _normalized(self, payload, store, resolved=None):
        currency = store.pos_config_id.currency_id
        reference = self._text(payload.get("partner_reference"), "partner_reference")
        aggregator = self._text(payload.get("aggregator"), "aggregator")
        aggregator_order_id = self._text(payload.get("aggregator_order_id"), "aggregator_order_id")
        status = self._text(payload.get("status"), "status").upper()
        if status not in self._STATUS_STATES:
            raise BlendAPIError("Unsupported Blend order status.", 422)
        if status in {"CANCELED", "CANCELLED", "REJECTED", "TIME_OUT"}:
            raise BlendAPIError("A terminal Blend order cannot be created as a new order.", 422)
        fulfillment = self._FULFILLMENT.get(
            self._text(payload.get("order_type"), "order_type").lower()
        )
        if not fulfillment:
            raise BlendAPIError("Unsupported Blend order_type.", 422)
        total = self._money(payload.get("total"), currency, "total")
        subtotal = self._money(payload.get("subtotal"), currency, "subtotal")
        tax = self._money(payload.get("tax"), currency, "tax")
        payment_code = self._PAYMENT_TYPES.get(
            str(payload["payment_type"]).strip().casefold()
        ) if "payment_type" in payload else "card"
        if not payment_code:
            raise BlendAPIError("Unsupported Blend payment_type.", 422)
        self.env["bs.delivery.payment.service"]._resolve_payment_method(
            store.connection_id, store, external_code=payment_code,
            external_source=aggregator,
            collected_by="restaurant" if payment_code == "cash" else "aggregator",
        )
        lines, items_subtotal, discount, vat_amount, accepted_total = self._lines(
            payload, store, subtotal=subtotal, tax=tax, total=total,
            resolved=resolved,
        )
        if Decimal(str(accepted_total)) != total:
            _logger.warning(
                "Blend whole-unit total match: reference=%s provider_total=%s "
                "odoo_total=%s difference=%s",
                reference, total, accepted_total, total - Decimal(str(accepted_total)),
            )
        customer_raw = payload.get("customer") or {}
        if not isinstance(customer_raw, dict) or set(customer_raw) - {"name", "phone", "email", "address", "note", "car"}:
            raise BlendAPIError("customer must be an object with supported fields.", 422)
        car_info = customer_raw.get("car")
        customer = {
            key: str(value).strip()
            for key, value in customer_raw.items()
            if value is not None and key != "car"
        }
        if isinstance(car_info, dict):
            car_desc = " ".join(
                str(car_info.get(k, "")).strip()
                for k in ("color", "car_brand", "plate_number", "plate_letters")
                if car_info.get(k)
            ).strip()
            if car_desc:
                curbside_note = f"Curbside Car: {car_desc}"
                customer["note"] = (
                    f"{customer.get('note', '')}\n{curbside_note}".strip()
                    if customer.get("note")
                    else curbside_note
                )
        if payload.get("note") is not None:
            if not isinstance(payload["note"], str):
                raise BlendAPIError("note must be text.", 422)
            existing = customer.get("note")
            customer["note"] = f"{payload['note']}\n{existing}".strip() if existing else payload["note"]
        payments = []
        if accepted_total:
            payment = {
                "external_payment_code": payment_code,
                "external_source": aggregator,
                "amount": accepted_total,
                "currency": currency.name,
                "collected_by": "restaurant" if payment_code == "cash" else "aggregator",
                "collection_status": "expected" if payment_code == "cash" else "collected",
            }
            if payment_code == "card":
                # Blend's order API has no payment reference. This identifier
                # is deterministic and explicitly marked as our assumption.
                payment["external_payment_reference"] = "blend-assumed:%s:%s" % (
                    store.connection_id.id, reference,
                )
            payments.append(payment)
        normalized = {
            "contract_version": 1,
            "external_order_id": reference,
            "external_reference": aggregator_order_id,
            "external_source": aggregator,
            "external_status": status,
            "currency": currency.name,
            "fulfillment_type": fulfillment,
            "price_basis": "tax_excluded",
            "customer": customer,
            "lines": lines,
            "totals": {
                "items_subtotal": items_subtotal,
                "discount_amount": discount,
                "fee_amount": 0,
                "vat_amount": vat_amount,
                "rounding_amount": 0,
                "amount_total": accepted_total,
            },
            "payments": payments,
        }
        schedule_at = payload.get("schedule_at")
        if status == "SCHEDULED" and not schedule_at:
            raise BlendAPIError("SCHEDULED orders require schedule_at.", 422)
        if schedule_at:
            if not isinstance(schedule_at, str):
                raise BlendAPIError("schedule_at must be a date and time.", 422)
            try:
                scheduled = datetime.fromisoformat(schedule_at)
                if not scheduled.tzinfo:
                    timezone_name = (
                        store.company_id.resource_calendar_id.tz
                        or store.company_id.partner_id.tz
                    )
                    if not timezone_name:
                        raise BlendAPIError("Configure the branch company timezone for scheduled orders.", 422)
                    scheduled = scheduled.replace(tzinfo=ZoneInfo(timezone_name))
            except (ValueError, ZoneInfoNotFoundError) as error:
                raise BlendAPIError("Invalid Blend schedule_at or branch timezone.", 422) from error
            normalized["scheduled_at"] = scheduled.isoformat()
        return normalized

    def receive_order(self, payload, authorization):
        if not isinstance(payload, dict) or set(payload) - self._ORDER_FIELDS:
            raise BlendAPIError("The Blend order must contain supported JSON fields.", 422)
        store = self._store(payload, authorization)
        return self._receive_order_for_store(payload, store)

    def _receive_order_for_store(self, payload, store, *, retry=False):
        """Resume a previously authenticated order without replaying its token."""
        try:
            self = self.with_env(store.connection_id._blend_execution_environment())
        except DeliveryAuthenticationError as error:
            raise BlendAPIError(str(error), 403) from error
        store = store.with_env(self.env)
        connection = store.connection_id.with_company(store.company_id)
        try:
            with self.env.cr.savepoint():
                resolved = {}
                normalized = self._normalized(payload, store, resolved=resolved)
                serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":"), default=str)
                digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
                event_id = str(payload.get("event_id") or "").strip()
                key = hashlib.sha256((
                    "blend-order:%s:%s:%s" % (
                        connection.id, normalized["external_order_id"], event_id or digest,
                    )
                ).encode()).hexdigest()
                event, _created = self.env["bs.delivery.webhook.event"].sudo()._get_or_create_event(
                    connection, {
                        "connection_id": connection.id,
                        "store_binding_id": store.id,
                        "signature_verified": True,
                        "event_type": "order.created",
                        "event_id": event_id or False,
                        "external_id": normalized["external_order_id"],
                        "idempotency_key": key,
                        "payload_digest": digest,
                        "payload_size": len(serialized.encode("utf-8")),
                        "raw_payload": serialized,
                        "payload_preview": serialize_masked_payload(serialized),
                        "state": "processing",
                    },
                )
                order = self.env["bs.delivery.order.service"].with_context(
                    bs_blend_api_order_id=str(uuid.uuid4()),
                    bs_blend_resolved_products=resolved,
                )._receive_order(connection, store, normalized, event=event)
                if order.pos_processing_state == "blocked" and not retry:
                    raise BlendAPIError(
                        order.pos_processing_error or "Blend accounting needs review.", 422,
                    )
                if (not retry and order.state == "accepted"
                        and order.pos_processing_state != "waiting_session") and (
                    not order.pos_order_id.account_move
                    or order.pos_order_id.account_move.state != "posted"
                ):
                    raise BlendAPIError("Blend acceptance has no posted invoice.", 422)
                event.write({
                    "state": "done", "processed_at": fields.Datetime.now(),
                    "binding_id": order.binding_id.id,
                })
                return {
                    "success": True,
                    "message": "Order created successfully",
                    "order_id": order.blend_api_order_id,
                }
        except DeliveryWebhookConflictError as error:
            raise BlendAPIError(str(error), 409) from error
        except DeliveryBusinessError as error:
            status = 409 if "conflict" in error.code else 422
            raise BlendAPIError(str(error), status) from error
        except (UserError, ValidationError) as error:
            raise BlendAPIError(str(error), 422) from error

    def update_order(self, payload, authorization):
        if not isinstance(payload, dict) or set(payload) - self._STATUS_FIELDS:
            raise BlendAPIError("The Blend status must contain supported JSON fields.", 422)
        store = self._store(payload, authorization)
        return self._update_order_for_store(payload, store)

    def _update_order_for_store(self, payload, store):
        """Apply a status update already tied to an authenticated Blend store."""
        try:
            self = self.with_env(store.connection_id._blend_execution_environment())
        except DeliveryAuthenticationError as error:
            raise BlendAPIError(str(error), 403) from error
        store = store.with_env(self.env)
        reference = self._text(payload.get("order_id"), "order_id")
        status = self._text(payload.get("status"), "status").upper()
        target = self._STATUS_STATES.get(status)
        if not target:
            raise BlendAPIError("Unsupported Blend order status.", 422)
        connection = store.connection_id.with_company(store.company_id)
        order = self.env["bs.delivery.order"].sudo().with_company(store.company_id).search([
            ("connection_id", "=", connection.id),
            ("store_binding_id", "=", store.id),
            ("blend_api_order_id", "=", reference),
            ("contract_version", "=", 1),
        ], limit=1)
        if not order:
            raise BlendAPIError("Blend order was not found in this branch.", 404)
        with self.env.cr.savepoint():
            self.env.cr.execute("SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE", [order.id])
            order.invalidate_recordset()
            serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), default=str)
            digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            event_id = str(payload.get("event_id") or "").strip()
            key = hashlib.sha256((
                "blend-status:%s:%s:%s" % (connection.id, reference, event_id or digest)
            ).encode()).hexdigest()
            try:
                event, _created = self.env["bs.delivery.webhook.event"].sudo()._get_or_create_event(
                    connection, {
                        "connection_id": connection.id,
                        "store_binding_id": store.id,
                        "signature_verified": True,
                        "event_type": "order.updated",
                        "event_id": event_id or False,
                        "external_id": order.external_order_id,
                        "idempotency_key": key,
                        "payload_digest": digest,
                        "payload_size": len(serialized.encode("utf-8")),
                        "raw_payload": serialized,
                        "payload_preview": serialize_masked_payload(serialized),
                        "state": "processing",
                    },
                )
                if order.state in ("delivered", "rejected", "cancelled") and target != order.state:
                    raise BlendAPIError("A terminal Blend order cannot change status.", 409)
                if target in self._PROGRESSION and order.state in self._PROGRESSION:
                    if self._PROGRESSION.index(target) < self._PROGRESSION.index(order.state):
                        event.write({
                            "state": "done", "processed_at": fields.Datetime.now(),
                            "binding_id": order.binding_id.id,
                        })
                        return self._status_result(order, reference)
                self.env["bs.delivery.order.service"]._apply_provider_update(
                    connection, store, order.external_order_id,
                    {
                        "contract_version": 1,
                        "state": target,
                        "occurred_at": datetime.now(timezone.utc).isoformat(),
                        "external_status": status,
                    }, event=event,
                )
                event.write({
                    "state": "done", "processed_at": fields.Datetime.now(),
                    "binding_id": order.binding_id.id,
                })
                return self._status_result(order, reference)
            except DeliveryWebhookConflictError as error:
                raise BlendAPIError(str(error), 409) from error
            except DeliveryBusinessError as error:
                raise BlendAPIError(str(error), 409) from error
            except (UserError, ValidationError) as error:
                raise BlendAPIError(str(error), 422) from error

    @staticmethod
    def _status_result(order, reference):
        return {
            "success": True,
            "message": "Order updated successfully",
            "order_id": reference,
            "status": (order.external_status or order.state).upper(),
        }


class BlendDeliverySyncLog(models.Model):
    _inherit = "bs.delivery.sync.log"

    @api.depends("status", "operation", "direction", "request_method", "request_url")
    def _compute_can_force_send(self):
        super()._compute_can_force_send()
        for log in self:
            if (log.status == "failed" and log.operation == "blend:create_order"
                    and log.direction == "incoming" and log.request_method == "POST"
                    and log.request_url == "/api/orders"):
                log.can_force_send = True

    def _force_send_existing_order(self):
        if self.operation == "blend:create_order":
            return self._blend_forced_existing_order()
        return super()._force_send_existing_order()

    def _force_send_unlinked_failure(self):
        if self.operation != "blend:create_order":
            return super()._force_send_unlinked_failure()
        return self.with_context(
            bs_blend_force_totals=True, bs_blend_force_send=True,
        )._retry_unlinked_failure()

    def _blend_forced_existing_order(self):
        """The delivery order already created from this Blend request, if any."""
        self.ensure_one()
        try:
            payload = json.loads(self.request_payload or "")
        except (TypeError, ValueError):
            return self.env["bs.delivery.order"]
        reference = str((payload or {}).get("partner_reference") or "").strip() \
            if isinstance(payload, dict) else ""
        if not reference:
            return self.env["bs.delivery.order"]
        return self.env["bs.delivery.order"].sudo().search([
            ("connection_id", "=", self.connection_id.id),
            ("external_order_id", "=", reference),
        ], limit=1)

    def _retry_unlinked_failure(self):
        self.ensure_one()
        if self.operation not in ("blend:create_order", "blend:update_order"):
            return super()._retry_unlinked_failure()
        expected_method, expected_url = (
            ("POST", "/api/orders") if self.operation == "blend:create_order"
            else ("PUT", "/api/order/update")
        )
        if (self.direction != "incoming" or self.request_method != expected_method
                or self.request_url != expected_url
                or self.connection_id.provider_id.code != "blend"):
            raise UserError("This Blend log is not a verified order request.")
        if (not self.request_payload or "...[truncated]" in self.request_payload
                or "********" in self.request_payload):
            raise UserError("The saved Blend request is incomplete; ask Blend to resend it.")
        try:
            payload = json.loads(self.request_payload)
        except (TypeError, ValueError) as error:
            raise UserError("The saved Blend request is not valid JSON.") from error
        if not isinstance(payload, dict):
            raise UserError("The saved Blend order must be a JSON object.")
        intake = self.env["bs.delivery.blend.intake"]
        allowed_fields = (intake._ORDER_FIELDS if self.operation == "blend:create_order"
                          else intake._STATUS_FIELDS)
        if set(payload) - allowed_fields:
            raise UserError("The saved Blend order contains unsupported fields.")
        stores = self.env["bs.delivery.blend.webhook.service"]._resolve_stores(
            {"branch_id": payload.get("branch_id")}, connection=self.connection_id,
        )
        store = stores.ensure_one()
        if str(payload.get("brand_id") or "").strip() != (
                store.remote_vendor_id or self.connection_id.blend_brand_id or "").strip():
            raise UserError("The Blend brand does not match this branch.")
        if self.operation == "blend:create_order":
            # Blend sends a rejected order up to three times: once one of its
            # logs has been retried, the others refer to the same order.
            existing = self._blend_forced_existing_order()
            if (existing and existing.store_binding_id == store
                    and existing.pos_processing_state not in ("blocked", "waiting_session")):
                return {"delivery_order_id": existing.id,
                        "pos_order_id": existing.pos_order_id.id,
                        "result": {"already_processed": True}}
            result = intake._receive_order_for_store(payload, store, retry=True)
            order_domain = [("external_order_id", "=", str(payload.get("partner_reference")))]
        else:
            result = intake._update_order_for_store(payload, store)
            order_domain = [("blend_api_order_id", "=", str(payload.get("order_id")))]
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection_id.id),
            ("store_binding_id", "=", store.id),
            *order_domain,
        ], limit=1)
        if not order:
            raise UserError("The Blend retry did not create a delivery order.")
        if (order.state == "received" and order.pos_processing_state != "blocked"
                and self.connection_id.delivery_accept_action == "accept"):
            order._apply_initial_acceptance_policy()
        if order.pos_processing_state in ("blocked", "waiting_session"):
            order.action_retry_pos_processing()
        if order.pos_processing_state == "blocked":
            raise UserError("Blend POS processing still needs review.")
        forced = bool(self.env.context.get("bs_blend_force_send"))
        if forced and (order.pos_order_id or order.state == "received"):
            # A received order now waits on the branch's POS delivery board
            # (manual acceptance branches).
            # Cashier and kitchen only need the POS order; a payment or invoice
            # still pending stays visible on the order for accounting review.
            return {"delivery_order_id": order.id, "pos_order_id": order.pos_order_id.id,
                    "result": result}
        if order.state in ("accepted", "preparing", "ready", "picked_up"):
            invoice = order.pos_order_id.account_move
            if (order.pos_processing_state != "done" or not order.pos_order_id
                    or order.pos_order_id.state != "done" or not invoice
                    or invoice.state != "posted"
                    or not order.currency_id.is_zero(invoice.amount_residual)):
                raise UserError("Blend POS payment and invoice are not complete yet.")
        return {"delivery_order_id": order.id, "pos_order_id": order.pos_order_id.id,
                "result": result}
