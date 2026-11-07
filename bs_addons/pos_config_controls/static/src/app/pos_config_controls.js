import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { patch } from "@web/core/utils/patch";

patch(PosOrder.prototype, {
    setup() {
        super.setup(...arguments);
        if (this.config?.always_to_invoice) {
            this.to_invoice = true;
        }
    },

    setToInvoice(toInvoice) {
        if (this.config?.always_to_invoice) {
            if (!this.to_invoice) {
                return super.setToInvoice(true);
            }
            return;
        }
        return super.setToInvoice(toInvoice);
    },
});

patch(PosStore.prototype, {
    createNewOrder(data = {}) {
        const order = super.createNewOrder(...arguments);
        if (this.config.default_customer_id && !data.partner_id) {
            order.setPartner(this.config.default_customer_id);
        }
        return order;
    },
});

patch(OrderPaymentValidation.prototype, {
    shouldDownloadInvoice() {
        if (this.pos.config.disable_auto_invoice_pdf_download) {
            return false;
        }
        return super.shouldDownloadInvoice(...arguments);
    },
});
