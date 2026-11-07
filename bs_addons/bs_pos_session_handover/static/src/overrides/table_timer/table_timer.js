import { FloorScreen } from "@pos_restaurant/app/screens/floor_screen/floor_screen";
import { SplitBillScreen } from "@pos_restaurant/app/screens/split_bill_screen/split_bill_screen";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { onMounted, onWillUnmount, useState } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";

const { DateTime } = luxon;
const TICK_MS = 15000;

function toMillis(value) {
    if (!value) {
        return null;
    }
    if (typeof value === "string") {
        const dt = DateTime.fromSQL(value, { zone: "utc" });
        return dt.isValid ? dt.toMillis() : null;
    }
    if (value.toMillis) {
        return value.toMillis();
    }
    if (value instanceof Date) {
        return value.getTime();
    }
    return null;
}

patch(PosStore.prototype, {
    /** Service start of a table order: server opening time, else local creation. */
    pinoGetServiceStart(order) {
        return toMillis(order?.pino_opened_at) ?? toMillis(order?.date_order);
    },

    /** Earliest service start among the open orders of a table. */
    pinoGetTableServiceStart(table) {
        const starts = (table?.getOrders?.() || [])
            .map((order) => this.pinoGetServiceStart(order))
            .filter((value) => value);
        return starts.length ? Math.min(...starts) : null;
    },

    /** "H:MM" elapsed since the table service started. */
    pinoFormatTableTimer(table, now = Date.now()) {
        const start = this.pinoGetTableServiceStart(table);
        if (!start) {
            return "";
        }
        const minutes = Math.max(Math.floor((now - start) / 60000), 0);
        const hours = Math.floor(minutes / 60);
        return `${hours}:${String(minutes % 60).padStart(2, "0")}`;
    },
});

patch(FloorScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.pinoClock = useState({ now: Date.now() });
        let timer = null;
        onMounted(() => {
            timer = setInterval(() => (this.pinoClock.now = Date.now()), TICK_MS);
        });
        onWillUnmount(() => clearInterval(timer));
    },
});

patch(SplitBillScreen.prototype, {
    async _createNewSplitOrder(originalOrder, newOrderName, curOrderUuid) {
        const result = await super._createNewSplitOrder(...arguments);
        const newUuid = originalOrder?.uiState?.splittedOrderUuid;
        const newOrder = result?.uuid
            ? result
            : newUuid && this.pos.models["pos.order"].getBy("uuid", newUuid);
        if (newOrder && newOrder !== originalOrder) {
            newOrder.pino_split_from_uuid = originalOrder.uuid;
            if (originalOrder.pino_opened_at) {
                newOrder.pino_opened_at = originalOrder.pino_opened_at;
            }
        }
        return result;
    },
});
