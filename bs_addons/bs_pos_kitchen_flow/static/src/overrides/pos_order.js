import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";
import { getKitchenFlowTrace } from "@bs_pos_kitchen_flow/utils/kitchen_flow_trace";

patch(PosOrder.prototype, {
    updateLastOrderChange() {
        const trace = getKitchenFlowTrace(this);
        if (!trace) {
            return super.updateLastOrderChange(...arguments);
        }
        const token = trace.begin("update_preparation_snapshot", "Update preparation snapshot", {
            category: "snapshot",
        });
        try {
            const result = super.updateLastOrderChange(...arguments);
            trace.end(token, "success", { line_count: this.lines?.length || 0 });
            return result;
        } catch (error) {
            trace.end(token, "error", undefined, error);
            throw error;
        }
    },

    updateSavedQuantity() {
        const trace = getKitchenFlowTrace(this);
        if (!trace) {
            return super.updateSavedQuantity(...arguments);
        }
        const token = trace.begin("update_saved_quantity", "Update saved kitchen quantities", {
            category: "snapshot",
        });
        try {
            const result = super.updateSavedQuantity(...arguments);
            trace.end(token, "success", { line_count: this.lines?.length || 0 });
            return result;
        } catch (error) {
            trace.end(token, "error", undefined, error);
            throw error;
        }
    },
});

