import { ModifierProductPopup } from "@product_modifiers/js/Popups/ModifierProductPopup";
import { patch } from "@web/core/utils/patch";


patch(ModifierProductPopup.prototype, {
    setup(...args) {
        super.setup(...args);
        const modifierModel = this.pos.models["product.modifiers"];
        const templateModel = this.pos.models["product.template"];
        const visibleOptions = [];
        for (const option of this.props.products) {
            const rawId = option.modifier_line_id;
            const lineId = typeof rawId === "object" ? rawId.id : rawId;
            const line = modifierModel?.get(lineId);
            if (line) {
                Object.assign(option, {
                    blend_group_external_id: line.blend_group_external_id,
                    blend_is_required: line.blend_is_required,
                    blend_min_selection: line.blend_min_selection,
                    blend_max_selection: line.blend_max_selection,
                    blend_max_per_quantity: line.blend_max_per_quantity,
                    blend_group_available: line.blend_group_available,
                });
            }
            const templateRelation = option.product?.product_tmpl_id;
            const templateId =
                templateRelation && typeof templateRelation === "object"
                    ? templateRelation.id
                    : templateRelation;
            const template =
                templateRelation && typeof templateRelation === "object"
                    ? templateRelation
                    : templateModel?.get(templateId);
            if (option.blend_group_available !== false && template?.available_in_pos !== false) {
                visibleOptions.push(option);
            }
        }
        this.props.products.splice(0, this.props.products.length, ...visibleOptions);
    },

    _getSelectedModifierQuantity(modifierLineId) {
        const selector = this._getSelectorConfig(modifierLineId);
        const groupId = selector?.blend_group_external_id;
        if (!groupId) {
            return super._getSelectedModifierQuantity(modifierLineId);
        }
        const groupedLineIds = new Set(
            this.props.products
                .filter((modifier) => modifier.blend_group_external_id === groupId)
                .map((modifier) => String(modifier.modifier_line_id))
        );
        return this.modifiers_list
            .filter((modifier) => groupedLineIds.has(String(modifier.modifier_line_id)))
            .reduce((total, modifier) => total + (modifier.selection_quantity || 1), 0);
    },

    SelectExtraModifier(event) {
        const selectionData = this._getSelectionData(event.currentTarget);
        const selector = this._getSelectorConfig(selectionData.modifierLineId);
        const maximumPerOption = Number(selector?.blend_max_per_quantity || 0);
        const current = this.modifiers_list.find(
            (modifier) => modifier.selection_key === selectionData.selectionKey
        );
        if (
            selector?.blend_group_external_id &&
            maximumPerOption > 0 &&
            (current?.selection_quantity || 0) >= maximumPerOption
        ) {
            this._showMaximumError({ ...selector, max_selector: maximumPerOption });
            return;
        }
        return super.SelectExtraModifier(event);
    },

    updateModifierQuantity(event) {
        if (event.currentTarget.getAttribute("data-modifier-cart-action") === "increase") {
            const selectionKey = event.currentTarget.getAttribute("data-modifier-key");
            const selection = this.modifiers_list.find(
                (modifier) => modifier.selection_key === selectionKey
            );
            const selector = selection
                ? this._getSelectorConfig(selection.modifier_line_id)
                : null;
            const maximumPerOption = Number(selector?.blend_max_per_quantity || 0);
            if (
                selector?.blend_group_external_id &&
                maximumPerOption > 0 &&
                (selection?.selection_quantity || 0) >= maximumPerOption
            ) {
                this._showMaximumError({ ...selector, max_selector: maximumPerOption });
                return;
            }
        }
        return super.updateModifierQuantity(event);
    },
});

