import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { drainBlendKitchenPrint } from "./blend_kitchen_worker";

patch(PosStore.prototype, {
    async setup(...args) {
        await super.setup(...args);
        if (!this.config.blend_enabled) {
            return;
        }
        const drain = () => drainBlendKitchenPrint(this);
        this.data.connectWebSocket("BLEND_KITCHEN_PRINT", (payload) => {
            if (payload.warning) {
                this._blendKitchenNotify(payload.reference, payload.warning);
            }
            void drain();
        });
        // Covers startup, missed bus notifications and temporarily offline devices.
        this._blendKitchenTimer = setInterval(drain, 10000);
        void drain();
    },

    _blendKitchenConnectionLost() {
        if (this._blendKitchenCloseConnectionWarning) {
            return;
        }
        this._blendKitchenCloseConnectionWarning = this.notification.add(
            _t("Connection to Odoo was interrupted. Checking Blend kitchen printing will resume automatically when the connection returns."),
            { type: "warning", sticky: true }
        );
    },

    _blendKitchenConnectionRestored() {
        // Only close our connection notice; printer/job warnings still need review.
        this._blendKitchenCloseConnectionWarning?.();
        this._blendKitchenCloseConnectionWarning = null;
    },

    _blendKitchenNotify(reference, message) {
        this.notification.add(
            _t("Blend kitchen printing needs review for %(reference)s. Open the order's Blend Kitchen Printing section before retrying. %(message)s", {
                reference: reference || _t("pending orders"), message,
            }),
            { type: "warning", sticky: true }
        );
    },
});
