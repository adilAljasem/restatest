/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog, AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { receiptLineGrouper } from "@point_of_sale/app/models/utils/order_change";

/* ------------------------------------------------------------------ */
/*  Store: resend logic                                                */
/* ------------------------------------------------------------------ */
patch(PosStore.prototype, {
    /** Kitchen printers configured on this POS. */
    bsHasKitchenPrinters() {
        return Boolean(this.unwatched?.printers?.length);
    },

    /** Preparation display (Enterprise pos_preparation_display) configured. */
    bsHasKitchenDisplay() {
        return Boolean(this.models["pos.prep.display"]?.length);
    },

    /** Paid order that can be resent. */
    bsCanResend(order) {
        return Boolean(
            order?.finalized && order.getOrderlines().some((l) => l.getQuantity() > 0)
        );
    },

    /** Mark resend on server (count + chatter). Works offline (returns 0). */
    async bsRegisterResend(order, channel) {
        if (typeof order.id !== "number") {
            return 0;
        }
        try {
            return await this.data.call("pos.order", "bs_register_kitchen_resend", [
                order.id,
                channel,
            ]);
        } catch {
            return 0;
        }
    },

    /** Full list of the order's lines, shaped like a preparation change. */
    bsBuildFullChange(order) {
        const lines = order
            .getOrderlines()
            .filter((l) => l.getQuantity() > 0)
            .map((orderline) => {
                const product = orderline.getProduct();
                return {
                    uuid: orderline.uuid,
                    name: orderline.getFullProductName(),
                    basic_name: orderline.product_id.name,
                    isCombo: Boolean(orderline.combo_line_ids?.length),
                    combo_parent_uuid: orderline.combo_parent_id?.uuid,
                    product_id: product.id,
                    attribute_value_names: orderline.attribute_value_ids.map((a) => a.name),
                    quantity: orderline.getQuantity(),
                    note: orderline.getNote(),
                    customer_note: orderline.getCustomerNote(),
                    pos_categ_id: product.pos_categ_ids[0]?.id ?? 0,
                    pos_categ_sequence: product.pos_categ_ids[0]?.sequence ?? 0,
                    display_name: product.display_name,
                    group: receiptLineGrouper.getGroup(orderline),
                };
            });
        return {
            new: lines,
            cancelled: [],
            noteUpdate: [],
            // order notes are already printed in the header via getOrderData
            internal_note: false,
            general_customer_note: false,
        };
    },

    /** Re-print the whole order on kitchen printers with a RESEND banner. */
    async bsResendToKitchenPrinter(order) {
        const count = await this.bsRegisterResend(order, "print");
        order.uiState.bsResend = { count: count || "" };
        try {
            // reprint=true → not pushed to lastPrints, failed printers get Retry popup
            return await this.printChanges(order, [this.bsBuildFullChange(order)], true);
        } finally {
            delete order.uiState.bsResend;
        }
    },

    /** Re-send the whole order to the preparation display. */
    async bsResendToKitchenDisplay(order) {
        const count = await this.bsRegisterResend(order, "display");
        const saved = JSON.parse(JSON.stringify(order.last_order_preparation_change || {}));
        order.uiState.bsResend = { count: count || "" };
        // Empty snapshot → every line is seen as "new" by the standard flow
        order.last_order_preparation_change = { ...saved, lines: {} };
        try {
            await this.sendOrderInPreparationUpdateLastChange(order, {
                byPassPrint: true,
                bsResend: true,
            });
        } finally {
            delete order.uiState.bsResend;
            if (!Object.keys(order.last_order_preparation_change?.lines || {}).length) {
                order.last_order_preparation_change = saved;
            }
        }
    },

    /** Add the resend flag to kitchen-ticket data. */
    getOrderData(order, reprint) {
        const data = super.getOrderData(...arguments);
        if (order.uiState?.bsResend) {
            data.bs_resend = true;
            data.bs_resend_count = order.uiState.bsResend.count;
        }
        return data;
    },
});

/* ------------------------------------------------------------------ */
/*  Orders screen: buttons                                             */
/* ------------------------------------------------------------------ */
patch(TicketScreen.prototype, {
    bsShowResendButtons(order) {
        return (
            this.pos.bsCanResend(order) &&
            (this.pos.bsHasKitchenPrinters() || this.pos.bsHasKitchenDisplay())
        );
    },

    bsConfirm(title, body) {
        return new Promise((resolve) => {
            this.dialog.add(ConfirmationDialog, {
                title,
                body,
                confirmLabel: _t("Resend"),
                confirm: () => resolve(true),
                cancel: () => resolve(false),
            });
        });
    },

    async bsOnResendPrint(order) {
        const ok = await this.bsConfirm(
            _t("Kitchen print"),
            _t("Re-print order %s in the kitchen? It will be marked as RESEND.", order.getName())
        );
        if (!ok || order.uiState.bsBusy) {
            return;
        }
        order.uiState.bsBusy = true;
        try {
            const printed = await this.pos.bsResendToKitchenPrinter(order);
            if (printed) {
                this.env.services.notification.add(_t("Order resent to kitchen printer"), { type: "success" });
            }
        } finally {
            order.uiState.bsBusy = false;
        }
    },

    async bsOnResendDisplay(order) {
        const ok = await this.bsConfirm(
            _t("Send to kitchen display"),
            _t("Re-send order %s to the kitchen display?", order.getName())
        );
        if (!ok || order.uiState.bsBusy) {
            return;
        }
        order.uiState.bsBusy = true;
        try {
            await this.pos.bsResendToKitchenDisplay(order);
            this.env.services.notification.add(_t("Order resent to kitchen display"), { type: "success" });
        } catch (e) {
            this.dialog.add(AlertDialog, {
                title: _t("Resend failed"),
                body: e?.message || _t("Check the connection and try again."),
            });
        } finally {
            order.uiState.bsBusy = false;
        }
    },
});
