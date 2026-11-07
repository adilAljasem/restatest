/** @odoo-module **/

import { ComboConfiguratorPopup } from "@point_of_sale/app/components/popups/combo_configurator_popup/combo_configurator_popup";
import { patch } from "@web/core/utils/patch";

patch(ComboConfiguratorPopup.prototype, {
    setup() {
        super.setup();
        if (!this.props.line) {
            for (const combo of this.props.productTemplate.combo_ids) {
                for (const item of combo.combo_item_ids) {
                    this.state.qty[combo.id][item.id] = item.default_qty || 0;
                }
            }
        }
    },

    autoSelectSingleChoices() {
        for (const combo of this.props.productTemplate.combo_ids) {
            if (combo.combo_item_ids.length === 1) {
                const [item] = combo.combo_item_ids;
                const selectedQuantity = Math.max(this.state.qty[combo.id][item.id], 1);
                this.state.qty[combo.id][item.id] = Math.min(
                    selectedQuantity,
                    this._maximumItemQuantity(item)
                );
            }
        }
    },

    isConfirmButtonEnabled() {
        return Object.keys(this.state.qty).every((comboId) => {
            const combo = this.pos.models["product.combo"].get(comboId);
            const quantity = this.totalQuantityForCombo(comboId);
            return quantity >= combo.qty_min && quantity <= combo.qty_max;
        });
    },

    _maximumItemQuantity(comboItem) {
        return comboItem.qty_max > 0
            ? Math.min(comboItem.qty_max, comboItem.combo_id.qty_max)
            : comboItem.combo_id.qty_max;
    },

    isItemPlusDisabled(comboItem) {
        return (
            this.totalQuantityForCombo(comboItem.combo_id.id) >= comboItem.combo_id.qty_max ||
            this.state.qty[comboItem.combo_id.id][comboItem.id] >=
                this._maximumItemQuantity(comboItem)
        );
    },

    async onClickSimpleProduct(comboItem, combo) {
        this.resetSingleQtyMaxCombo(combo);
        const currentQuantity = this.state.qty[combo.id][comboItem.id];
        if (
            this.totalQuantityForCombo(combo.id) < combo.qty_max &&
            currentQuantity < this._maximumItemQuantity(comboItem)
        ) {
            this.state.qty[combo.id][comboItem.id] = currentQuantity + 1;
        }
    },

    async onClickConfigurableProduct(product, comboItem, combo) {
        const currentQuantity = this.state.qty[combo.id][comboItem.id];
        if (combo.qty_max > 1 && currentQuantity >= this._maximumItemQuantity(comboItem)) {
            return;
        }
        return super.onClickConfigurableProduct(product, comboItem, combo);
    },

    setQuantity(comboItem, quantity) {
        const combo = comboItem.combo_id;
        const currentQuantity = this.state.qty[combo.id][comboItem.id];
        const availableGroupQuantity =
            combo.qty_max - this.totalQuantityForCombo(combo.id) + currentQuantity;
        const maximumQuantity = Math.min(
            availableGroupQuantity,
            this._maximumItemQuantity(comboItem)
        );
        this.state.qty[combo.id][comboItem.id] = Math.max(
            0,
            Math.min(quantity, maximumQuantity)
        );
    },
});
