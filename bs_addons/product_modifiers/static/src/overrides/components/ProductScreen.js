/** @odoo-module **/

import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { ModifierProductPopup } from "@product_modifiers/js/Popups/ModifierProductPopup";
import { SliceProductPopup } from "@product_modifiers/js/Popups/SliceProductPopup";
import {
    buildModifierProductOptions,
    buildSliceProductOptions,
    getModifierChildren,
    getModifierSelectionQuantity,
    validateSelectorQuantity,
} from "@product_modifiers/utils/modifier_utils";

function selectorErrorMessage(selectorConfig, error) {
    if (error === "min") {
        return {
            title: _t("Minimum Selector"),
            body: _t(
                "Select at least %(minimum)s modifiers for this item.",
                { minimum: selectorConfig.min_selector }
            ),
        };
    }
    return {
        title: _t("Maximum Selector"),
        body: _t(
            "Select no more than %(maximum)s modifiers for this item.",
            { maximum: selectorConfig.max_selector }
        ),
    };
}

patch(OrderSummary.prototype, {
    async ediModifiers(orderline) {
        const parentLine = orderline || this.currentOrder.getSelectedOrderline();
        if (!parentLine?.product_id) {
            this.dialog.add(AlertDialog, {
                title: _t("Error"),
                body: _t("Please select a product to modify"),
            });
            return;
        }

        const product = parentLine.product_id;
        if (!product.is_modifier) {
            return;
        }

        const modifierProducts = buildModifierProductOptions(
            this.pos.models,
            product.modifier_ids || []
        );
        const sliceProducts = buildSliceProductOptions(
            this.pos.models,
            product.slice_product_ids || product.raw?.slice_product_ids || []
        );
        if (!modifierProducts.length && !sliceProducts.length) {
            return;
        }

        let selections = [];
        if (modifierProducts.length) {
            const payload = await makeAwaitable(this.dialog, ModifierProductPopup, {
                title: product.display_name,
                products: modifierProducts,
                product,
                editModifiers: true,
            });
            if (!payload) {
                return;
            }
            selections = payload;
        }

        if (sliceProducts.length) {
            const sidePayload = await makeAwaitable(this.dialog, SliceProductPopup, {
                title: _t("Add Sides"),
                products: sliceProducts,
            });
            if (sidePayload) {
                selections = selections.concat(sidePayload);
            }
        }

        for (const childLine of [...getModifierChildren(this.currentOrder, parentLine)]) {
            this.currentOrder.removeOrderline(childLine);
        }
        await this.pos.addModifierLines(parentLine, selections);
    },

    _setValue(val) {
        const order = this.pos.getOrder();
        const selectedLine = order.getSelectedOrderline();
        if (this.pos.numpadMode === "quantity" && selectedLine?.parent_line_id) {
            const parentLine = order.lines.find(
                (line) => String(line.id) === String(selectedLine.parent_line_id)
            );
            const modifierLine = parentLine?.product_id.modifier_ids.find(
                (modifier) =>
                    Number(modifier.id) === Number(selectedLine.modifier_line_id)
            );
            if (modifierLine?.apply_selector) {
                const requestedQuantity = val === "remove" ? 0 : Number(val);
                if (Number.isFinite(requestedQuantity)) {
                    const currentSelectionQuantity = Number(
                        selectedLine.modifier_selection_qty || selectedLine.qty || 0
                    );
                    const selectionPerUnit = selectedLine.qty
                        ? currentSelectionQuantity / Math.abs(selectedLine.qty)
                        : 1;
                    const requestedSelectionQuantity =
                        Math.abs(requestedQuantity) * selectionPerUnit;
                    const nextSelectionQuantity =
                        getModifierSelectionQuantity(
                            order,
                            parentLine,
                            modifierLine.id
                        ) -
                        currentSelectionQuantity +
                        requestedSelectionQuantity;
                    const error = validateSelectorQuantity(
                        modifierLine,
                        nextSelectionQuantity
                    );
                    if (error) {
                        this.dialog.add(
                            AlertDialog,
                            selectorErrorMessage(modifierLine, error)
                        );
                        return;
                    }
                }
            }
        }
        return super._setValue(...arguments);
    },
});
