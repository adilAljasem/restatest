/** @odoo-module **/

import { ProductTemplate } from "@point_of_sale/app/models/product_template";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";
import { recordId } from "@product_modifiers/utils/modifier_utils";

/**
 * Modifier (add-on) products are only picked from the Modifier popup.
 * In Odoo 19 the product grid and search go through
 * PosStore.filterExcludedProducts(), which skips templates listed in
 * getExcludedProductIds() or whose `canBeDisplayed` is false. The products
 * stay loaded, so the popup can still add them as order lines.
 */
function isFlaggedAsModifier(template) {
    if (!template) {
        return false;
    }
    if (template.use_as_modifier || template.raw?.use_as_modifier) {
        return true;
    }
    const variants = template.product_variant_ids || [];
    return variants.some((variant) => variant?.use_as_modifier);
}

patch(ProductTemplate.prototype, {
    get canBeDisplayed() {
        return super.canBeDisplayed && !isFlaggedAsModifier(this);
    },
});

patch(PosStore.prototype, {
    /**
     * Template ids of every product used as a modifier line, collected from
     * the loaded modifier data itself so it does not depend on the
     * `use_as_modifier` flag being present on the template record.
     */
    getModifierTemplateIds() {
        const ids = new Set();
        const productModel = this.models["product.product"];
        const templateModel = this.models["product.template"];

        for (const line of this.models["product.modifiers"]?.getAll?.() || []) {
            const product = productModel?.get(recordId(line.name ?? line.raw?.name));
            const tmplId = recordId(product?.product_tmpl_id);
            if (tmplId) {
                ids.add(tmplId);
            }
        }
        for (const group of this.models["modifiers.groups"]?.getAll?.() || []) {
            for (const tmpl of group.modifier_ids || group.raw?.modifier_ids || []) {
                const tmplId = recordId(tmpl);
                if (tmplId) {
                    ids.add(tmplId);
                }
            }
        }
        for (const product of productModel?.getAll?.() || []) {
            if (product.use_as_modifier) {
                const tmplId = recordId(product.product_tmpl_id);
                if (tmplId) {
                    ids.add(tmplId);
                }
            }
        }
        for (const tmpl of templateModel?.getAll?.() || []) {
            if (isFlaggedAsModifier(tmpl)) {
                ids.add(tmpl.id);
            }
        }
        return ids;
    },

    getExcludedProductIds() {
        return [...super.getExcludedProductIds(...arguments), ...this.getModifierTemplateIds()];
    },
});
