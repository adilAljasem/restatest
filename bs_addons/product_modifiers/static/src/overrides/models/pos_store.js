import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { ModifierProductPopup } from "@product_modifiers/js/Popups/ModifierProductPopup";
import { SliceProductPopup } from "@product_modifiers/js/Popups/SliceProductPopup";
import {
    buildModifierProductOptions,
    buildSliceProductOptions,
    getModifierChildren,
    recordId,
} from "@product_modifiers/utils/modifier_utils";

patch(PosStore.prototype, {
    async addModifierLines(parentLine, selections) {
        for (const selection of selections) {
            const extraProduct = this.models["product.product"].get(
                recordId(selection.product)
            );
            if (!extraProduct) {
                continue;
            }
            const extras = {
                ...(selection.extras || {}),
                parent_line_id: String(parentLine.id),
                modifier_line_id: selection.modifier_line_id || 0,
                modifier_selection_qty:
                    selection.modifier_line_id
                        ? selection.selection_quantity ?? selection.quantity ?? 1
                        : 0,
            };
            const lineValues = {
                product_tmpl_id: extraProduct.product_tmpl_id,
                // Odoo 19 defaults to the first template variant unless the
                // concrete product is passed explicitly.
                product_id: extraProduct,
                qty: selection.quantity ?? 1,
            };
            if (selection.price !== undefined) {
                lineValues.price_unit = selection.price;
            } else {
                lineValues.price_extra = selection.price_extra || 0;
            }
            await this.addLineToCurrentOrder(
                lineValues,
                {
                    description: selection.attribute_value,
                    merge: false,
                    extras,
                },
                false
            );
        }
    },

    async addLineToCurrentOrder(vals, opts = {}, configure = true) {
        const parentLine = await super.addLineToCurrentOrder(vals, opts, configure);
        if (!parentLine || !configure) {
            return parentLine;
        }

        const mainProduct = parentLine.product_id;
        if (!mainProduct?.is_modifier) {
            return parentLine;
        }

        const modifierProducts = buildModifierProductOptions(
            this.models,
            mainProduct.modifier_ids || []
        );
        const sliceProducts = buildSliceProductOptions(
            this.models,
            mainProduct.slice_product_ids || mainProduct.raw?.slice_product_ids || []
        );
        if (!modifierProducts.length && !sliceProducts.length) {
            return parentLine;
        }

        let selections = [];
        if (modifierProducts.length) {
            const payload = await makeAwaitable(this.dialog, ModifierProductPopup, {
                title: mainProduct.display_name,
                products: modifierProducts,
                product: mainProduct,
                editModifiers: false,
            });
            // Skip keeps the parent line and does not open another selection dialog.
            if (!payload) {
                return parentLine;
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

        await this.addModifierLines(parentLine, selections);
        return parentLine;
    },
});

patch(PosOrder.prototype, {
    removeOrderline(line) {
        for (const childLine of [...getModifierChildren(this, line)]) {
            this.removeOrderline(childLine);
        }
        return super.removeOrderline(...arguments);
    },
});

patch(PosOrderline.prototype, {
    setup() {
        const result = super.setup(...arguments);
        this.parent_line_id = this.parent_line_id || "";
        this.portion = this.portion || "";
        this.modifier_line_id = this.modifier_line_id || 0;
        this.modifier_selection_qty = this.modifier_selection_qty || 0;
        return result;
    },

    canBeMergedWith(orderline) {
        const canMerge = super.canBeMergedWith(...arguments);
        if (!canMerge) {
            return false;
        }
        return !(
            this.parent_line_id ||
            orderline.parent_line_id ||
            this.product_id?.is_modifier ||
            orderline.product_id?.is_modifier
        );
    },

    getDisplayClasses() {
        return {
            ...super.getDisplayClasses(...arguments),
            modifier: Boolean(this.parent_line_id),
            "border-start border-3 ms-4": Boolean(this.parent_line_id),
        };
    },

    set_parent_line_id(lineId) {
        this.parent_line_id = String(lineId || "");
    },

    get_parent_line_id() {
        return this.parent_line_id;
    },

    set_portion(portion) {
        this.portion = String(portion || "");
    },

    get_portion() {
        return this.portion;
    },

    set_modifier_line_id(lineId) {
        this.modifier_line_id = Number(lineId) || 0;
    },

    get_modifier_line_id() {
        return this.modifier_line_id;
    },

    setQuantity(quantity) {
        const previousQuantity = Number(this.qty || 0);
        const previousSelectionQuantity = Number(
            this.modifier_selection_qty || previousQuantity
        );
        const result = super.setQuantity(...arguments);
        if (
            result === true &&
            this.modifier_line_id &&
            previousQuantity &&
            previousSelectionQuantity
        ) {
            this.modifier_selection_qty =
                previousSelectionQuantity * Math.abs(this.qty / previousQuantity);
        }
        return result;
    },

    setOptions(options) {
        const result = super.setOptions(...arguments);
        const extras = options?.extras || {};
        if (extras.parent_line_id !== undefined) {
            this.set_parent_line_id(extras.parent_line_id);
        }
        if (extras.portion !== undefined) {
            this.set_portion(extras.portion);
        }
        if (extras.modifier_line_id !== undefined) {
            this.set_modifier_line_id(extras.modifier_line_id);
        }
        if (extras.modifier_selection_qty !== undefined) {
            this.modifier_selection_qty = Number(extras.modifier_selection_qty) || 0;
        }
        return result;
    },
});
