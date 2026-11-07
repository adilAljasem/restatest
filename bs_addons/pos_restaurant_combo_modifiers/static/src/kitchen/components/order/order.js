/** @odoo-module **/

import { Order } from "@pos_enterprise/app/components/order/order";
import { patch } from "@web/core/utils/patch";

patch(Order.prototype, {
    _acknowledgeKitchenPrepLine(prepLine) {
        if (!prepLine?.modifier_snapshot_initialized) {
            return;
        }
        prepLine.modifier_display_data = (prepLine.modifier_snapshot_data || []).map(
            (snapshotEntry) => {
                const entry = { ...snapshotEntry };
                delete entry.change_status;
                return entry;
            }
        );
    },

    _acknowledgeKitchenState(state) {
        const prepLine = state.prep_line_id;
        this._acknowledgeKitchenPrepLine(prepLine);
        this._acknowledgeKitchenPrepLine(prepLine.combo_parent_id);
        for (const comboLine of prepLine.combo_line_ids || []) {
            this._acknowledgeKitchenPrepLine(comboLine);
        }
    },

    changeOrderlineStatus(state) {
        const acceptingChange = state.todo;
        const result = super.changeOrderlineStatus(state);
        if (acceptingChange) {
            this._acknowledgeKitchenState(state);
        }
        return result;
    },

    changeStateStageAnimation(states) {
        const result = super.changeStateStageAnimation(states);
        for (const state of states) {
            this._acknowledgeKitchenState(state);
        }
        return result;
    },
});
