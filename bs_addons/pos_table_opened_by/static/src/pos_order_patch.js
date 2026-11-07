import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";

patch(PosOrder.prototype, {
    getOpenedByName() {
        // قبل أول حفظ على السيرفر: الكاشير الحالي هو من فتح الطلب
        return this.opened_by_name || this.getCashierName() || "";
    },
});
