import { ComboConfiguratorPopup } from "@point_of_sale/app/components/popups/combo_configurator_popup/combo_configurator_popup";
import { patch } from "@web/core/utils/patch";

patch(ComboConfiguratorPopup.prototype, {
    shouldShowCombo(combo) {
        return (combo.blend_external_id && combo.blend_min_selection === 0) || super.shouldShowCombo(combo);
    },

    autoSelectSingleChoices() {
        this.props.productTemplate.combo_ids
            .filter((combo) => !combo.blend_external_id || combo.blend_min_selection > 0)
            .forEach((combo) => {
                if (combo.combo_item_ids.length === 1 && !combo.combo_item_ids[0].product_id.isConfigurable()) {
                    this.state.qty[combo.id][combo.combo_item_ids[0].id] = 1;
                }
            });
    },

    isConfirmButtonEnabled() {
        return super.isConfirmButtonEnabled() && this.props.productTemplate.combo_ids.every(
            (combo) => !combo.blend_external_id ||
                this.totalQuantityForCombo(combo.id) >= combo.blend_min_selection
        );
    },

    setQuantity(comboItem, quantity) {
        const max = comboItem.combo_id.blend_max_per_quantity;
        return super.setQuantity(comboItem, max ? Math.min(quantity, max) : quantity);
    },

    async onClickSimpleProduct(comboItem, combo) {
        const current = this.state.qty[combo.id][comboItem.id];
        if (combo.blend_external_id && combo.blend_min_selection === 0 && combo.qty_max === 1 && current) {
            this.state.qty[combo.id][comboItem.id] = 0;
            return;
        }
        if (combo.blend_external_id && current >= combo.blend_max_per_quantity) {
            return;
        }
        return super.onClickSimpleProduct(comboItem, combo);
    },
});
