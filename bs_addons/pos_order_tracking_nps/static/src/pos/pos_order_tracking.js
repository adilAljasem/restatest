import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { generateQRCodeDataUrl } from "@point_of_sale/utils";

function randomToken() {
    // 16 random bytes, base64url (same entropy as the server-side secrets.token_urlsafe(16))
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return btoa(String.fromCharCode(...bytes))
        .replace(/\+/g, "-")
        .replace(/\//g, "_")
        .replace(/=+$/, "");
}

patch(PosOrder.prototype, {
    setup(vals) {
        super.setup(...arguments);
        // Generated on the device so the QR can be printed even before the order is synced.
        if (!this.tracking_token) {
            this.tracking_token = randomToken();
        }
    },
    get trackingUrl() {
        return `${this.config._base_url}/track/${this.tracking_token}`;
    },
    /**
     * Keep in sync with pos.config._tracking_order_eligible (server side).
     */
    get isTrackingEligible() {
        const config = this.config;
        if (!config?.tracking_enabled) {
            return false;
        }
        const raw = config.raw || {};
        const presetIds = raw.tracking_preset_ids || [];
        if (config.use_presets && presetIds.length && !presetIds.includes(this.preset_id?.id)) {
            return false;
        }
        if (config.module_pos_hr) {
            // Only defined when pos_order_tracking_nps_hr is installed.
            const employeeIds = raw.tracking_employee_ids || [];
            return !employeeIds.length || employeeIds.includes(this.employee_id?.id);
        }
        const userIds = raw.tracking_user_ids || [];
        return !userIds.length || userIds.includes(this.user_id?.id);
    },
    get showTrackingQr() {
        const isRefund = this.lines?.some((l) => l.qty < 0) && this.priceIncl < 0;
        return Boolean(this.tracking_token && !isRefund && this.isTrackingEligible);
    },
});

patch(OrderReceipt.prototype, {
    get trackingQrCode() {
        return generateQRCodeDataUrl(this.order.trackingUrl, { width: 160, height: 160 });
    },
});
