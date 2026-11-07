/** @odoo-module **/

/*
 * A tiny holder for POS services that model records cannot reach on their own.
 *
 * WHY THIS EXISTS
 *
 * The on-premise module shows the qty-lock warning with
 * `this.pos.notification.add(...)` from inside PosOrderline.setQuantity(). That
 * throws: model records are Proxies that expose `models`, `config` and `session`,
 * but **not** `pos` and **not** `env`. Core never writes `this.pos` on a record on
 * either platform, 19.0 included, so the on-premise call is broken there too.
 * Verified on a live database,
 * where `line.pos`, `order.pos`, `line.env` and `order.env` are all undefined and
 * the numpad raised
 *   TypeError: Cannot read properties of undefined (reading 'notification')
 * which surfaces to the cashier as Odoo's generic "Oops!" dialog.
 *
 * PosStore does have the service, so it publishes it here once at startup and the
 * model patches read it back. Kept deliberately minimal: this is a bridge for a
 * missing back-reference, not a place to accumulate global state.
 */

export const bonatRuntime = {
    /** The POS notification service, set by bonat_store.js at startup. */
    notification: null,
    /** The PosStore itself, for model-layer code that needs pos.bonat. */
    pos: null,
    /** The UI service, for blocking input while a Bonat request is in flight. */
    ui: null,
};

/**
 * Run `fn` with the POS UI blocked (Odoo's standard spinner overlay), so a
 * cashier cannot tap again or change the order while a Bonat network call is
 * pending. Always unblocks, whatever `fn` does. Never wrap a popup with this:
 * the overlay would sit on top of it.
 */
export async function withBonatBusy(fn) {
    const ui = bonatRuntime.ui;
    ui?.block?.();
    try {
        return await fn();
    } finally {
        ui?.unblock?.();
    }
}

/**
 * Show a warning to the cashier, falling back to the console when the POS has not
 * finished starting up (or when a unit test drives the models directly).
 */
export function bonatWarn(message) {
    if (bonatRuntime.notification?.add) {
        bonatRuntime.notification.add(message, { type: "warning" });
        return;
    }
    console.warn("[bonat]", message);
}

/** Short confirmation for the cashier (same fallback as bonatWarn). */
export function bonatInfo(message) {
    if (bonatRuntime.notification?.add) {
        bonatRuntime.notification.add(message, { type: "info" });
        return;
    }
    console.info("[bonat]", message);
}
