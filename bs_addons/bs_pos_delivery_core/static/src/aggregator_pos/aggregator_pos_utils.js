/** @odoo-module **/

export const DEFAULT_PROVIDER_COLORS = Object.freeze([
    "#7c3aed",
    "#0f766e",
    "#2563eb",
    "#c2410c",
    "#be123c",
    "#4f46e5",
    "#15803d",
]);

const BOARD_STATE_ALIASES = Object.freeze({
    new: "received",
    pending: "received",
    awaiting: "received",
    received: "received",
    accepted: "accepted",
    confirmed: "accepted",
    preparing: "preparing",
    in_preparation: "preparing",
    ready: "ready",
    ready_for_pickup: "ready",
    ready_for_pick_up: "ready",
    picked: "picked_up",
    picked_up: "picked_up",
});

export function normalizeNonNegativeInteger(value, fallback = 0) {
    const number = Number(value);
    return Number.isInteger(number) && number >= 0 ? number : fallback;
}

export function normalizeBoardState(value) {
    return BOARD_STATE_ALIASES[String(value || "").trim().toLowerCase()] || false;
}

export function safeProviderColor(value, index = 0) {
    const color = String(value || "").trim();
    if (/^#[0-9a-f]{3}([0-9a-f]{3})?$/i.test(color)) {
        return color;
    }
    const numericColor = Number(value);
    const colorIndex = Number.isInteger(numericColor)
        ? Math.abs(numericColor) % DEFAULT_PROVIDER_COLORS.length
        : Math.abs(normalizeNonNegativeInteger(index)) % DEFAULT_PROVIDER_COLORS.length;
    return DEFAULT_PROVIDER_COLORS[colorIndex];
}

export function providerInitials(value) {
    return String(value || "")
        .trim()
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((part) => part[0]?.toUpperCase())
        .join("");
}

export function orderNotificationKey(order = {}) {
    const value =
        order.order_id ||
        order.id ||
        order.event_id ||
        order.provider_reference ||
        order.platform_reference ||
        order.external_reference;
    return value ? `order:${String(value)}` : false;
}

export const KITCHEN_PRINT_RETRY_DELAYS_MS = Object.freeze([1500, 3000]);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Compute the kitchen changes of the whole order, as if nothing had been sent.
 *
 * An automatic delivery job is the order's first kitchen ticket; the
 * server-side job state, not the device's change tracking, guarantees it is
 * printed once.  When another device or the kitchen display had already
 * marked the lines as sent, the normal diff was empty and the job failed
 * with no ticket at all.  The baseline is swapped only for this synchronous
 * computation and restored at once.
 */
export function fullOrderKitchenChanges(order, categories, changesToOrder) {
    const saved = order.last_order_preparation_change;
    order.last_order_preparation_change = {
        ...(saved && typeof saved === "object" ? saved : {}),
        lines: {},
        general_customer_note: "",
        internal_note: "",
    };
    try {
        return changesToOrder(order, categories, false);
    } finally {
        order.last_order_preparation_change = saved;
    }
}

/**
 * Print one ticket, retrying when the printer does not confirm it.
 *
 * The kitchen printer is often busy for a moment (the cashier receipt of the
 * same order, another ticket), which used to turn the only automatic attempt
 * into "Print Error" with no ticket in the kitchen.
 */
export async function printWithRetry(print, delays = KITCHEN_PRINT_RETRY_DELAYS_MS, wait = sleep) {
    let lastError = "";
    for (let attempt = 0; attempt <= delays.length; attempt++) {
        if (attempt) {
            await wait(delays[attempt - 1]);
        }
        try {
            const result = await print();
            if (result?.successful) {
                return { result, error: "" };
            }
            lastError = result?.message?.body || "Printer did not confirm the ticket.";
        } catch (error) {
            lastError = error?.message || String(error);
        }
    }
    return { result: false, error: lastError };
}

/** Print an automatic kitchen job using Odoo's receipt methods, without its retry popup. */
export async function printKitchenReceipts(pos, order, orderChange, printers, options = {}) {
    let printed = false;
    const errors = [];
    for (const printer of printers) {
        const printerName = printer.config?.name || "Printer";
        try {
            const { orderData, changes } = pos.generateOrderChange(
                order, orderChange, printer.config.product_categories_ids, false
            );
            const receipts = await pos.generateReceiptsDataToPrint(
                orderData, changes, orderChange
            );
            for (const receipt of receipts) {
                const { result, error } = await printWithRetry(
                    () => pos.printOrderChanges(receipt, printer),
                    options.retryDelays,
                    options.wait
                );
                if (result) {
                    printed = true;
                    if (result.warningCode) {
                        try {
                            pos.displayPrinterWarning?.(result, printerName);
                        } catch {
                            // A UI warning cannot undo a confirmed physical print.
                        }
                    }
                } else {
                    errors.push(`${printerName}: ${error}`);
                }
            }
        } catch (error) {
            errors.push(`${printerName}: ${error?.message || String(error)}`);
        }
    }
    return { successful: printed && !errors.length, errors };
}

export function parseDeliveryDate(value) {
    if (!value) {
        return false;
    }
    const text = String(value).trim();
    const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(text)
        ? `${text.replace(" ", "T")}Z`
        : text;
    const timestamp = Date.parse(normalized);
    return Number.isFinite(timestamp) ? new Date(timestamp) : false;
}

export function orderAgeSeconds(order = {}, now = Date.now()) {
    const suppliedAge = Number(order.age_seconds);
    if (Number.isFinite(suppliedAge)) {
        return Math.max(0, suppliedAge);
    }
    if (!order.received_at) {
        return false;
    }
    const receivedAt = parseDeliveryDate(order.received_at);
    return receivedAt ? Math.max(0, (now - receivedAt.getTime()) / 1000) : false;
}
