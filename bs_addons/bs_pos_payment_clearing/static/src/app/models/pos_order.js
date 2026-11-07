import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";

patch(PosOrder.prototype, {
    addPaymentline(paymentMethod) {
        const result = super.addPaymentline(...arguments);
        const partner = paymentMethod.partner_id;

        if (result.status && paymentMethod.split_transactions && partner && this.partner_id !== partner) {
            this.setPartner(partner);
            result.data.setAmount(0);
            result.data.setAmount(this.getDefaultAmountDueToPayIn(paymentMethod));
        }
        return result;
    },
});
