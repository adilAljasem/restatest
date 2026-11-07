import { patch } from "@web/core/utils/patch";
import { Mutex } from "@web/core/utils/concurrency";
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { CCDispatchDialog } from "./cc_dispatch_dialog";

const POLL_INTERVAL = 30000; // ms – safety net when the websocket is down

function errorMessage(e) {
    return e?.data?.message || e?.message || _t("Unexpected error");
}

export function ccBeep() {
    try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) {
            return;
        }
        const ctx = new Ctx();
        [0, 0.25, 0.5].forEach((start) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = "sine";
            osc.frequency.value = 880;
            gain.gain.setValueAtTime(0.25, ctx.currentTime + start);
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + start + 0.2);
            osc.connect(gain).connect(ctx.destination);
            osc.start(ctx.currentTime + start);
            osc.stop(ctx.currentTime + start + 0.2);
        });
        setTimeout(() => ctx.close(), 1200);
    } catch {
        // audio not allowed – ignore
    }
}

patch(PosStore.prototype, {
    // ------------------------------------------------------------------
    // Common
    // ------------------------------------------------------------------
    get ccIsTerminal() {
        return Boolean(this.config?.cc_is_terminal);
    },
    get ccIsBranch() {
        return Boolean(this.config?.cc_is_branch) && !this.ccIsTerminal;
    },
    /** Unpaid call center orders currently loaded on this device. */
    get ccWaitingOrders() {
        return this.models["pos.order"]
            .filter((o) => o.cc_ref && o.state === "draft" && !o.finalized)
            .sort((a, b) => (a.id || 0) - (b.id || 0));
    },

    /** Called once when the POS UI is mounted (see CCBranchButton). */
    ccStart() {
        if (this._ccStarted) {
            return;
        }
        this._ccStarted = true;
        this._ccMutex = new Mutex();
        this._ccKnown = new Set(this.ccWaitingOrders.map((o) => o.id));
        try {
            this.data.connectWebSocket("CC_ORDER_REMOVED", (payload) => this.ccOnRemoved(payload));
            if (this.ccIsBranch) {
                this.data.connectWebSocket("CC_ORDER", () => this.ccSyncBranch(true));
            }
        } catch (e) {
            console.warn("pos_call_center: websocket unavailable, polling only", e);
        }
        if (this.ccIsBranch) {
            this.ccSyncBranch(false);
            this._ccInterval = setInterval(() => this.ccSyncBranch(true), POLL_INTERVAL);
        }
    },

    /** An order left this POS (sent / transferred elsewhere): drop the local copy. */
    ccOnRemoved({ uuid }) {
        const order = this.models["pos.order"].getBy("uuid", uuid);
        if (!order || order.finalized) {
            return;
        }
        const wasCurrent = this.getOrder()?.uuid === uuid;
        this.removePendingOrder(order);
        this.data.localDeleteCascade(order);
        if (wasCurrent) {
            const next = this.addNewOrder();
            if (this.router.state.current === "ProductScreen") {
                this.navigate("ProductScreen", { orderUuid: next.uuid });
            }
        }
    },

    // ------------------------------------------------------------------
    // Call center POS: "Payment" becomes "Send to Branch"
    // ------------------------------------------------------------------
    async pay() {
        if (this.ccIsTerminal) {
            return this.ccDispatch(this.getOrder());
        }
        return super.pay(...arguments);
    },
    async submitOrder() {
        if (this.ccIsTerminal) {
            return this.ccDispatch(this.getOrder());
        }
        return super.submitOrder(...arguments);
    },

    async ccDispatch(order) {
        if (!order || order.isEmpty()) {
            this.notification.add(_t("Add products to the order first."), { type: "warning" });
            return;
        }
        let targets;
        try {
            targets = await this.data.call("pos.call.center", "cc_get_targets", [this.config.id]);
        } catch (e) {
            this.dialog.add(AlertDialog, { title: _t("Call Center"), body: errorMessage(e) });
            return;
        }
        if (targets.require_customer && !order.getPartner()) {
            const partner = await this.selectPartner(order);
            if (!partner) {
                this.notification.add(_t("Please select the customer before sending the order."), {
                    type: "warning",
                });
                return;
            }
        }
        if (!targets.branches.length) {
            this.dialog.add(AlertDialog, {
                title: _t("No open branch"),
                body: _t("No branch of your call center has an open session right now."),
            });
            return;
        }
        const choice = await makeAwaitable(this.dialog, CCDispatchDialog, { order, targets });
        if (!choice) {
            return;
        }
        this.env.services.ui.block();
        try {
            await this.syncAllOrders({ orders: [order], force: true, throw: true });
            if (!order.isSynced) {
                throw new Error(_t("The order could not be saved, check the connection."));
            }
            const res = await this.data.call("pos.order", "cc_dispatch", [
                order.id,
                choice.configId,
                choice.zoneId || false,
            ]);
            this.removePendingOrder(order);
            this.data.localDeleteCascade(order);
            const next = this.addNewOrder();
            this.navigate("ProductScreen", { orderUuid: next.uuid });
            this.notification.add(
                _t("Order %(ref)s sent to %(branch)s – order number %(number)s", {
                    ref: res.cc_ref,
                    branch: res.branch,
                    number: res.tracking_number,
                }),
                { type: "success", sticky: false }
            );
        } catch (e) {
            this.dialog.add(AlertDialog, { title: _t("Order not sent"), body: errorMessage(e) });
        } finally {
            this.env.services.ui.unblock();
        }
    },

    // ------------------------------------------------------------------
    // Branch POS: receive the orders and send them to the kitchen
    // ------------------------------------------------------------------
    ccSyncBranch(alert = true) {
        if (!this._ccMutex) {
            return;
        }
        return this._ccMutex.exec(() => this._ccSyncBranch(alert));
    },

    async _ccSyncBranch(alert) {
        if (this.data.network?.offline) {
            return;
        }
        let pending;
        try {
            pending = await this.data.call("pos.order", "cc_branch_pending", [this.config.id]);
        } catch {
            return;
        }
        const fresh = [];
        for (const item of pending) {
            let order = this.models["pos.order"].get(item.id);
            if (item.kitchen_state === "pending" && this.config.cc_auto_send_kitchen) {
                let claimed = false;
                try {
                    claimed = await this.data.call("pos.order", "cc_claim_kitchen", [item.id]);
                } catch {
                    continue;
                }
                order = await this._ccLoadOrder(item.id);
                if (claimed && order) {
                    try {
                        await this.sendOrderInPreparationUpdateLastChange(order);
                    } catch (e) {
                        console.error("pos_call_center: kitchen sending failed", e);
                        await this.data
                            .call("pos.order", "cc_release_kitchen", [item.id])
                            .catch(() => {});
                    }
                }
            } else if (!order) {
                order = await this._ccLoadOrder(item.id);
            }
            if (order && !this._ccKnown.has(order.id)) {
                this._ccKnown.add(order.id);
                fresh.push(order);
            }
        }
        if (alert && fresh.length) {
            ccBeep();
            for (const order of fresh) {
                this.notification.add(
                    _t("New call center order %(ref)s – %(customer)s", {
                        ref: order.cc_ref,
                        customer: order.getPartner()?.name || "",
                    }),
                    { type: "warning", title: _t("Call Center") }
                );
            }
        }
    },

    async _ccLoadOrder(orderId) {
        try {
            await this.data.loadServerOrders([["id", "=", orderId]]);
        } catch (e) {
            console.error("pos_call_center: could not load order", orderId, e);
        }
        return this.models["pos.order"].get(orderId);
    },

    ccOpenOrder(order) {
        this.setOrder(order);
        this.navigate("ProductScreen", { orderUuid: order.uuid });
    },

    async ccTransfer(order, targetConfigId) {
        await this.data.call("pos.order", "cc_transfer", [order.id, targetConfigId]);
        this.ccOnRemoved({ uuid: order.uuid });
    },
});
