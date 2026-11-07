import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";


patch(PosStore.prototype, {
    async setup(...args) {
        await super.setup(...args);
        if (this.config.blend_enabled) {
            this.data.connectWebSocket(
                "BLEND_AVAILABILITY",
                this.onBlendAvailability.bind(this)
            );
        }
    },

    onBlendAvailability(payload) {
        if (!payload || payload.branch_id !== this.config.blend_branch_id) {
            return;
        }
        if (["item", "modifier"].includes(payload.kind) && payload.product_tmpl_id) {
            const template = this.models["product.template"].get(payload.product_tmpl_id);
            if (template) {
                template.available_in_pos = Boolean(payload.available);
            }
        } else if (payload.kind === "branch") {
            this.config.blend_available = Boolean(payload.available);
        }
        if (payload.kind === "menu_sync") {
            this.notification.add(
                _t("Blend menu was updated. Reload the POS to load new menu records."),
                { type: "info" }
            );
        } else if (payload.kind === "branch" && !payload.available) {
            this.notification.add(_t("This branch was paused from Blend."), {
                type: "warning",
            });
        }
    },
});


patch(PaymentScreen.prototype, {
    async validateOrder(isForceValidate = false) {
        if (this.pos.config.blend_enabled && !this.pos.config.blend_available) {
            this.dialog.add(AlertDialog, {
                title: _t("Blend Branch Paused"),
                body: _t(
                    "This branch is currently unavailable in Blend. Resume it before validating new orders."
                ),
            });
            return;
        }
        return super.validateOrder(isForceValidate);
    },
});
