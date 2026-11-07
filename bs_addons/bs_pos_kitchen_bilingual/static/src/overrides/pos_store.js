import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";
import { decorateKitchenOrderChange } from "@bs_pos_kitchen_bilingual/utils/bilingual_names";

patch(PosStore.prototype, {
    generateOrderChange(order, orderChange, categories, reprint = false) {
        decorateKitchenOrderChange(order, orderChange);
        return super.generateOrderChange(...arguments);
    },
});

