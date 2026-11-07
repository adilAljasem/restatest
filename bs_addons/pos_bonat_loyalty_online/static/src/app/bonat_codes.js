/** @odoo-module **/

/*
 * Several Bonat codes on one order (Foodics-style) — the pure part.
 *
 * No Odoo import on purpose: everything here is plain data in / data out, so it
 * is unit-tested with Node (tests/bonat_codes.test.mjs) and reused by the
 * models, the store and the button without any of them owning the rules.
 *
 * AN ORDER'S CODES
 *
 * order.bonatCodes is a list of entries, one per code, in the order they were
 * entered:
 *
 *   {
 *     code:     "645934",
 *     kind:     "order"    whole-order discount (Bonat type 1)
 *             | "product"  per-product discount (Bonat type 2; its lines carry
 *                          line.bonat_code === code)
 *             | "voucher"  Bonat voucher, paid with the voucher payment method
 *     rule:     { is_percentage, amount, cap }   kind "order" only
 *     amount:   Number                           kind "voucher" only
 *     lineUuids: [String]  uuids of its reward lines, kind "product" only
 *     currency: String                           kind "voucher" only
 *     state:    "pending"   applied on the order, not yet used with Bonat
 *             | "redeeming" the redeem call is in flight
 *             | "redeemed"  used with Bonat (cannot be undone)
 *             | "failed"    Bonat refused it, or could not be reached
 *     error:    String     last redeem error, "failed" only
 *     retriable: Boolean   "failed" because of the network, worth retrying
 *     legacy:   Boolean    restored from the single-code build (already redeemed)
 *   }
 *
 * WHEN A CODE IS USED WITH BONAT
 *
 * Entering a code only CHECKS it (reward-check) and applies the reward on the
 * order. The code is used (redeem) when the order is sent to the kitchen, in the
 * background — the cashier never waits for Bonat there. A code that is still not
 * used when the order is paid (never sent to the kitchen, or added afterwards)
 * is used at payment, all codes in parallel, before Odoo saves the order: a
 * discount is never given for a code Bonat did not accept.
 */

export const BONAT_CODE_KINDS = Object.freeze(["order", "product", "voucher"]);

/** Codes compare without surrounding spaces and without case. */
export function normalizeBonatCode(code) {
    return String(code ?? "").trim().toUpperCase();
}

export function makeBonatCodeEntry({
    code,
    kind,
    rule = null,
    amount = 0,
    currency = "",
    lineUuids = [],
}) {
    if (!BONAT_CODE_KINDS.includes(kind)) {
        throw new Error(`Unknown Bonat code kind: ${kind}`);
    }
    const entry = { code: String(code).trim(), kind, state: "pending" };
    if (kind === "order") {
        entry.rule = {
            is_percentage: !!rule?.is_percentage,
            amount: Number(rule?.amount) || 0,
            cap: Number(rule?.cap) || 0,
        };
    }
    if (kind === "voucher") {
        entry.amount = Number(amount) || 0;
        entry.currency = currency || "";
    }
    if (kind === "product") {
        // uuids of the order lines carrying the reward: synced with the
        // order, so every device can tell which lines belong to the code.
        entry.lineUuids = [...lineUuids];
    }
    return entry;
}

/**
 * Rebuild the code list of an order saved by the single-code build (one
 * appliedCode, one bonatDiscountRule, one voucher). That build redeemed the code
 * the moment it was entered, so the entry is restored as already redeemed.
 */
export function migrateLegacyBonatCodes(vals) {
    const code = vals?.appliedCode;
    if (!code) {
        return [];
    }
    let entry;
    if (vals.bonatVoucherMethod === "Voucher") {
        entry = makeBonatCodeEntry({
            code,
            kind: "voucher",
            amount: vals.bonatVoucherAmount,
            currency: vals.bonatVoucherCurrency,
        });
    } else if (vals.bonatDiscountRule) {
        entry = makeBonatCodeEntry({ code, kind: "order", rule: vals.bonatDiscountRule });
    } else {
        entry = makeBonatCodeEntry({ code, kind: "product" });
    }
    entry.state = "redeemed";
    entry.legacy = true;
    return [entry];
}

/** Restore a saved list: a redeem cut by a reload is retried, never assumed done. */
export function restoreBonatCodes(saved) {
    if (!Array.isArray(saved)) {
        return [];
    }
    return saved
        .filter((entry) => entry && entry.code && BONAT_CODE_KINDS.includes(entry.kind))
        .map((entry) => ({
            ...entry,
            state: entry.state === "redeeming" ? "pending" : entry.state || "pending",
        }));
}

export function findBonatCode(codes, code) {
    const wanted = normalizeBonatCode(code);
    return (codes || []).find((entry) => normalizeBonatCode(entry.code) === wanted);
}

/** Codes that still have to be used with Bonat (pending, or failed but retriable). */
export function bonatCodesToRedeem(codes) {
    return (codes || []).filter(
        (entry) => entry.state === "pending" || (entry.state === "failed" && entry.retriable)
    );
}

/** Codes Bonat refused for good — payment is blocked until they are removed. */
export function bonatCodesRefused(codes) {
    return (codes || []).filter((entry) => entry.state === "failed" && !entry.retriable);
}

/** Codes still not used after a redeem attempt (refused, or Bonat unreachable). */
export function bonatCodesNotRedeemed(codes) {
    return (codes || []).filter((entry) => entry.state !== "redeemed");
}

/** Sum of the voucher codes (failed ones excluded: they will be removed). */
export function bonatVoucherTotal(codes) {
    return (codes || [])
        .filter((entry) => entry.kind === "voucher" && entry.state !== "failed")
        .reduce((sum, entry) => sum + (Number(entry.amount) || 0), 0);
}

/**
 * Split several whole-order rules on an order worth `total` (tax included).
 *
 * The codes apply one after the other, in the order they were entered, each on
 * what is left after the previous ones — the way stacked discounts are read on
 * a receipt: 10% then 5 SR on 100 SR is 10 + 5 = 15, and two 50% codes are
 * 50 + 25, never 100. A fixed code never takes more than what is left, so the
 * order never goes below zero.
 *
 * @returns {{ amounts: number[], total: number, isFull: boolean }}
 *   amounts[i] is what rule i takes off (rounded to the currency), total their
 *   sum, isFull true when the order is fully discounted (use 100% so Odoo's
 *   engine lands on exactly 0.00).
 */
export function splitBonatOrderDiscount(total, rules, rounding = 0.01) {
    const round = (value) => Math.round(value / rounding) * rounding;
    let remaining = Math.max(Number(total) || 0, 0);
    const amounts = [];
    for (const rule of rules || []) {
        const amount = Number(rule?.amount) || 0;
        const cap = Number(rule?.cap) || 0;
        let wanted;
        if (rule?.is_percentage) {
            wanted = (remaining * Math.min(Math.max(amount, 0), 100)) / 100;
            if (cap > 0 && wanted > cap) {
                wanted = cap;
            }
        } else {
            wanted = Math.max(amount, 0);
        }
        const taken = round(Math.min(wanted, remaining));
        amounts.push(taken);
        remaining = round(remaining - taken);
    }
    const sum = round(amounts.reduce((a, b) => a + b, 0));
    return { amounts, total: sum, isFull: sum >= round(total) - rounding / 2 && sum > 0 };
}

/**
 * Apply a redeem result to an entry (mutates and returns it).
 * `result` is what BonatApi.redeem() returns.
 */
export function applyBonatRedeemResult(entry, result) {
    if (result?.success) {
        entry.state = "redeemed";
        entry.error = "";
        entry.retriable = false;
        entry.redeemedAt = new Date().toISOString();
    } else {
        entry.state = "failed";
        entry.error = (typeof result?.error === "string" && result.error) || "";
        entry.retriable = !!result?.retriable;
    }
    return entry;
}
