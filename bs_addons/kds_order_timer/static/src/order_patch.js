/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { registry } from "@web/core/registry";

const KdsScreen = registry.category("actions").get("resta_kds.screen");

function pad(n) {
    return String(n).padStart(2, "0");
}

/** 3725 s → 1:02:05, 125 s → 02:05 */
export function formatSeconds(total) {
    const s = Math.max(0, Math.floor(total || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`;
}

/** last three digits of the order number, so the expo can call it out */
export function shortNumber(ticket) {
    const digits = String(ticket.number || ticket.order_name || "").replace(/\D/g, "");
    return digits ? digits.slice(-3).padStart(3, "0") : ticket.number || ticket.order_name || "";
}

patch(KdsScreen.prototype, {
    elapsedText(ticket) {
        return formatSeconds(this.elapsed(ticket) / 1000);
    },
    kdsShortNumber(ticket) {
        return shortNumber(ticket);
    },
});
