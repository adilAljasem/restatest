/** @odoo-module */

// Order number of 3 digits, per POS (Odoo 19).
//
// The order number (tracking_number) is what the POS screen, the customer
// receipt and the kitchen ticket show. It is always 3 digits (001 ... 999);
// Odoo 19 itself puts the device number in front ("1005").
// - "Order Number Range" enabled on the POS: it runs from the start to the end
//   number of the settings and then starts again: 1-100 -> 001, 002 ... 100,
//   001 ... and starts again from the start number at each new session.
// - Disabled: 001 ... 999, then 001 again.
// It follows the order's invoice sequence (pos_reference "2610-1-000123" ->
// 123), so it is known offline, when the order is created, before the kitchen
// ticket is printed.

import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";

export function invoiceSequence(reference) {
    const match = String(reference || "").match(/(\d+)\D*$/);
    return match ? parseInt(match[1], 10) : 0;
}

export const ORDER_NUMBER_DIGITS = 3;
export const ORDER_NUMBER_MAX = 999;

/** 3-digit number of the order at position `sequence` (1, 2, 3...). */
export function cycleOrderNumber(sequence, start = 1, end = ORDER_NUMBER_MAX) {
    start = parseInt(start, 10);
    end = parseInt(end, 10);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 1 || end > ORDER_NUMBER_MAX || end <= start) {
        // Invalid range: plain 3-digit numbering.
        start = 1;
        end = ORDER_NUMBER_MAX;
    }
    const size = end - start + 1;
    const number = start + ((Math.max(parseInt(sequence, 10) || 1, 1) - 1) % size);
    return String(number).padStart(ORDER_NUMBER_DIGITS, "0");
}

/** Order number shown everywhere: always 3 digits. */
export function threeDigitOrderNumber(config, reference, base = 1) {
    const ranged = rangeOrderNumber(config, reference, base);
    if (ranged) {
        return ranged;
    }
    const sequence = invoiceSequence(reference);
    return sequence ? cycleOrderNumber(sequence) : "";
}

/**
 * @param {Object} config pos.config
 * @param {string} reference pos_reference of the new order
 * @param {number} base invoice sequence of the first order of the session
 *     on this device: numbering starts again from the start number at each
 *     new session.
 */
export function rangeOrderNumber(config, reference, base = 1) {
    if (!config?.order_number_range_enabled) {
        return "";
    }
    const sequence = invoiceSequence(reference);
    if (!sequence) {
        return "";
    }
    // Position of the order in the session: 1, 2, 3...
    const position = sequence >= base ? sequence - base + 1 : 1;
    return cycleOrderNumber(position, config.order_number_start, config.order_number_end);
}

const BASE_KEY = "odoo_pos_custom_branding.order_number_base";

function readBases() {
    try {
        return JSON.parse(localStorage.getItem(BASE_KEY)) || {};
    } catch {
        return {};
    }
}

function writeBases(bases) {
    try {
        localStorage.setItem(BASE_KEY, JSON.stringify(bases));
    } catch {
        // Storage unavailable: numbering still works for this page.
    }
}

/**
 * Invoice sequence of the first order of the session on this device. Saved
 * on the device (per POS and session), so it is kept after a reload and a
 * new session starts again from the start number.
 */
export function sessionBase(configId, sessionId, sequence, bases = readBases()) {
    const key = `${configId}-${sessionId}`;
    if (!bases[key]) {
        // Keep only the sessions of this POS opened most recently.
        for (const old of Object.keys(bases)) {
            if (old.startsWith(`${configId}-`) && old !== key) {
                delete bases[old];
            }
        }
        bases[key] = sequence;
        writeBases(bases);
    }
    return bases[key];
}

patch(PosStore.prototype, {
    setNextOrderRefs(order) {
        super.setNextOrderRefs(...arguments);
        const sequence = invoiceSequence(order.pos_reference);
        if (!sequence) {
            return;
        }
        const base = this.config?.order_number_range_enabled
            ? sessionBase(this.config.id, this.session?.id, sequence)
            : 1;
        const number = threeDigitOrderNumber(this.config, order.pos_reference, base);
        if (number) {
            order.tracking_number = number;
        }
    },
});
