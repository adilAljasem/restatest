import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";

patch(PosStore.prototype, {
    getDefaultPartnerId() {
        return this.config.default_partner_id || super.getDefaultPartnerId();
    },
});
