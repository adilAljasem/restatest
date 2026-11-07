import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";
import {
    buildOrderlineNameSnapshot,
    findSavedPreparationLine,
} from "@bs_pos_kitchen_bilingual/utils/bilingual_names";

patch(PosOrder.prototype, {
    updateLastOrderChange() {
        const result = super.updateLastOrderChange(...arguments);
        for (const orderline of this.lines) {
            const savedLine = findSavedPreparationLine(this, orderline.uuid);
            if (savedLine) {
                Object.assign(savedLine, buildOrderlineNameSnapshot(orderline));
            }
        }
        return result;
    },
});

