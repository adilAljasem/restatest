import OrderPaymentValidation from '@point_of_sale/app/utils/order_payment_validation';
import { patch } from '@web/core/utils/patch';

patch(OrderPaymentValidation.prototype, {
    shouldDownloadInvoice() {
        if (this.pos.config.defer_invoice_pdf_generation) {
            return false;
        }
        return super.shouldDownloadInvoice(...arguments);
    },
});
