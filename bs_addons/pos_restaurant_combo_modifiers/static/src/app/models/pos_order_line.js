/** @odoo-module **/

import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { formatCurrency } from "@web/core/currency";
import { patch } from "@web/core/utils/patch";

export function deleteModifierChildren(parentLine, options = {}) {
    for (const modifierLine of [...(parentLine.modifier_child_line_ids || [])]) {
        modifierLine.delete(options);
    }
}

export function syncModifierDiscounts(parentLine) {
    for (const modifierLine of parentLine.modifier_child_line_ids || []) {
        modifierLine.setDiscount(parentLine.discount);
    }
}

export function syncModifierQuantities(parentLine, previousQuantity) {
    for (const modifierLine of parentLine.modifier_child_line_ids || []) {
        const quantityPerParent =
            modifierLine.modifier_qty_per_parent ||
            (previousQuantity ? Math.abs(modifierLine.qty / previousQuantity) : 0);
        modifierLine.modifier_qty_per_parent = quantityPerParent;
        modifierLine.setQuantity(quantityPerParent * parentLine.qty, true);
    }
}

patch(PosOrderline.prototype, {
    get modifierReceiptDetails() {
        if (this.is_modifier_line) {
            return [];
        }
        const detailsByOption = new Map();
        for (const modifierLine of this.modifier_child_line_ids || []) {
            const option = modifierLine.modifier_option_id;
            const key = option?.id || modifierLine.modifier_option_name || modifierLine.uuid;
            if (!detailsByOption.has(key)) {
                detailsByOption.set(key, {
                    key,
                    name:
                        modifierLine.modifier_option_name ||
                        option?.name ||
                        modifierLine.full_product_name,
                    quantity: 0,
                    amount: 0,
                });
            }
            const detail = detailsByOption.get(key);
            detail.quantity += Math.abs(modifierLine.modifier_qty_per_parent || 0);
            detail.amount += modifierLine.displayPrice;
        }
        return [...detailsByOption.values()].map((detail) => ({
            ...detail,
            formattedPrice: this.currency.isZero(detail.amount)
                ? ""
                : formatCurrency(detail.amount, this.currency.id),
        }));
    },

    get modifierReceiptInstructions() {
        const instructionData = this.modifier_instruction_data || {};
        if (!Array.isArray(instructionData.instructions)) {
            return [];
        }
        return instructionData.instructions
            .filter((instruction) => instruction && typeof instruction === "object")
            .map((instruction, index) => ({
                key: `${instruction.option_id || "instruction"}-${index}`,
                name: instruction.name || instruction.kitchen_name || "",
                groupName: instruction.group_name || "",
                quantity: Number(instruction.quantity) || 1,
            }))
            .filter((instruction) => instruction.name);
    },

    canBeMergedWith(orderline) {
        if (
            this.is_modifier_line ||
            orderline.is_modifier_line ||
            (this.modifier_configuration_key || "") !==
                (orderline.modifier_configuration_key || "")
        ) {
            return false;
        }
        return super.canBeMergedWith(orderline);
    },

    isPosGroupable() {
        if (this.is_modifier_line || this.modifier_parent_line_id) {
            return false;
        }
        return super.isPosGroupable();
    },

    delete(opts = {}) {
        deleteModifierChildren(this, opts);
        return super.delete(opts);
    },

    _modifierUnitPrice(pricelist = this.order_id.pricelist_id) {
        const option = this.modifier_option_id;
        if (!option || this.modifier_is_included || option.price_mode === "included") {
            return 0;
        }
        if (option.price_mode === "fixed_extra") {
            return option.extra_price;
        }
        if (option.price_mode === "product_pricelist") {
            return this.product_id.getPrice(
                pricelist,
                Math.abs(this.qty),
                0,
                false,
                this.product_id
            );
        }
        return 0;
    },

    _recomputeModifierPrice(pricelist = this.order_id.pricelist_id) {
        if (this.is_modifier_line && !this.refunded_orderline_id) {
            this.setUnitPrice(this._modifierUnitPrice(pricelist));
        }
    },

    setDiscount(discount) {
        const result = super.setDiscount(discount);
        if (!this.is_modifier_line) {
            syncModifierDiscounts(this);
        }
        return result;
    },

    setQuantity(quantity, keepPrice) {
        const previousQuantity = this.qty;
        const result = super.setQuantity(quantity, keepPrice);
        if (result === true) {
            this._recomputeModifierPrice();
            syncModifierQuantities(this, previousQuantity);
        }
        return result;
    },
});
