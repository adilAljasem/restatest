/** @odoo-module **/

import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";
import { accountTaxHelpers } from "@account/helpers/account_tax";
import { BonatApi } from "@pos_bonat_loyalty_online/app/bonat_api";
import { bonatRuntime, bonatWarn } from "@pos_bonat_loyalty_online/app/bonat_runtime";
import { _t } from "@web/core/l10n/translation";
import {
    applyBonatRedeemResult,
    bonatCodesToRedeem,
    splitBonatOrderDiscount,
} from "@pos_bonat_loyalty_online/app/bonat_codes";
import {
    BONAT_API_URL_DEFAULT,
    BONAT_API_URL_STAGING_DEFAULT,
    BONAT_BACKUP_FIELDS,
    BONAT_BACKUP_PARAM,
    PARAM_API_URL,
    PARAM_API_URL_STAGING,
} from "@pos_bonat_loyalty_online/app/bonat_const";

/*
 * POS startup wiring.
 *
 * Replaces two Python hooks that cannot exist in an importable module:
 *
 *  1. res.company._load_pos_data_fields(), which appended
 *     enable_bonat_integration / bonat_api_key / bonat_merchant_id /
 *     bonat_merchant_name / bonat_demo_mode to the preloaded POS payload.
 *
 *     Manual x_ fields are NOT preloaded: this was measured on a real Odoo
 *     Online database, where `pos.config.x_bonat_spike_token` came back
 *     undefined from the payload while an ORM read of the same field succeeded.
 *     So the values are fetched here with one explicit read at startup.
 *
 *  2. product_template._load_pos_data_domain(), which force-included the Bonat
 *     discount product in the POS load even though it is available_in_pos=False.
 *     Replaced by pos.data.loadProductFromPos(), the standard client-side entry
 *     point for pulling a product into the registry on demand.
 *
 * Everything is defensive: a failure here must degrade Bonat to "disabled" and
 * must never stop the POS from opening.
 */
/**
 * Stable fingerprint of a menu payload, so an unchanged catalogue costs zero
 * network traffic on POS open. Only the fields Bonat's mapper consumes take part,
 * sorted by id, so record ordering and irrelevant field churn don't force pushes.
 */
async function bonatCatalogueHash(payload) {
    const stable = JSON.stringify({
        c: payload.categories.map((c) => [c.id, c.name]).sort((a, b) => a[0] - b[0]),
        p: payload.products
            .map((p) => [p.id, p.name, p.lst_price, p.pos_categ_ids[0]?.id || 0])
            .sort((a, b) => a[0] - b[0]),
    });
    if (window.crypto?.subtle) {
        const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(stable));
        return (
            "sha256-" +
            Array.from(new Uint8Array(digest))
                .map((b) => b.toString(16).padStart(2, "0"))
                .join("")
        );
    }
    // crypto.subtle exists only in secure contexts; a POS served over plain http
    // falls back to djb2. Collisions just cost one redundant (idempotent) push.
    let h = 5381;
    for (let i = 0; i < stable.length; i++) {
        h = ((h << 5) + h + stable.charCodeAt(i)) | 0;
    }
    return "djb2-" + (h >>> 0).toString(16) + "-" + stable.length;
}

/**
 * Upper bound on how long Bonat may delay POS opening. The configuration reads
 * keep running in the background past this point and fill pos.bonat when they
 * land; the POS itself never waits longer than this for Bonat.
 */
const BONAT_STARTUP_BUDGET_MS = 6000;

/** Resolve with the promise, or with `undefined` once `ms` has elapsed. */
function withinBudget(promise, ms) {
    let timer;
    const timeout = new Promise((resolve) => {
        timer = setTimeout(() => {
            console.warn(`[bonat] startup exceeded ${ms}ms, continuing without waiting`);
            resolve(undefined);
        }, ms);
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

/** Stable key for a set of taxes, used to match discount lines to tax groups. */
function bonatTaxKey(taxes) {
    return (taxes || [])
        .map((tax) => tax.id)
        .sort((a, b) => a - b)
        .join("_");
}

/** Tolerance for "did this value change" checks, well below any currency unit. */
const BONAT_EPSILON = 1e-6;

patch(PosStore.prototype, {
    async afterProcessServerData() {
        await super.afterProcessServerData(...arguments);
        // Must never block or break POS opening: see withinBudget().
        try {
            await withinBudget(this._bonatStartup(), BONAT_STARTUP_BUDGET_MS);
        } catch (error) {
            console.error("[bonat] startup failed, integration disabled:", error);
        }
        this._bonatRegisterDiscountListeners();
    },

    async _bonatStartup() {

        // Neutral default, so every consumer can read pos.bonat unconditionally.
        this.bonat = {
            enabled: false,
            apiKey: "",
            merchantId: "",
            merchantName: "",
            demoMode: false,
            // ADR 0003 on main (docs/adr/0003-odoo19-menu-sync-mode-toggle.md — this
            // branch's own docs/adr/0003-*.md is a different decision, see
            // docs/adr/README.md): neutral default matches res.company's own
            // no-guaranteed-value column (see data/bonat_company_fields.xml) —
            // anything other than the literal "template" reads as variant-level.
            menuSyncMode: "variant",
            apiUrl: BONAT_API_URL_DEFAULT,
            apiUrlStaging: BONAT_API_URL_STAGING_DEFAULT,
            discountProduct: null,
            voucherPaymentMethodId: null,
            loadError: null,
        };
        this.bonatApi = new BonatApi(() => this.bonat, this);

        // Publish services the model layer cannot reach: PosOrderline records have
        // no `pos` or `env` back-reference on this series. See bonat_runtime.js.
        bonatRuntime.notification = this.notification;
        bonatRuntime.pos = this;
        bonatRuntime.ui = this.env?.services?.ui || null;

        try {
            await this.loadBonatConfig();
        } catch (error) {
            console.error("[bonat] configuration load failed, integration disabled:", error);
            this.bonat.loadError = String(error?.message || error);
        }

        // Settings survival across module re-imports (see BONAT_BACKUP_PARAM):
        // a configured POS refreshes the backup; an unconfigured one attempts a
        // restore. Both are best-effort — cashiers lack the needed rights and
        // fail silently; a manager or admin opening the POS does the real work.
        if (this.bonat.enabled || this.bonat.apiKey || this.bonat.merchantId) {
            this.mirrorBonatConfig().catch(() => {});
        } else if (!this.bonat.loadError) {
            try {
                await this.healBonatConfigFromBackup();
            } catch {
                // Expected for non-admin users; the settings page has its own heal.
            }
        }

        // Does the server have pos.order.x_bonat_codes (data/bonat_order_fields.xml)?
        // It only exists once the module was UPGRADED; until then the code list
        // must not be sent with the order, or Odoo would refuse to save it.
        this.bonat.ordersHaveCodesField = false;
        this.env?.services?.orm?.silent
            ?.call("pos.order", "fields_get", [["x_bonat_codes"]], { attributes: ["type"] })
            .then((fields) => {
                this.bonat.ordersHaveCodesField = !!fields?.x_bonat_codes;
                if (!this.bonat.ordersHaveCodesField) {
                    console.warn(
                        "[bonat] pos.order.x_bonat_codes is missing: upgrade the module. " +
                            "Codes still work on this device but are not shared with other devices."
                    );
                }
            })
            .catch((error) => console.warn("[bonat] could not check x_bonat_codes:", error));

        if (this.bonat.enabled) {
            // Fire-and-forget: menu sync must never delay or block POS opening.
            this.syncBonatMenu().catch((error) =>
                console.error("[bonat] menu sync failed:", error)
            );
        }
    },

    // ------------------------------------------------------------------
    // Whole-order (type 1) discount — built on Odoo's own tax engine
    // ------------------------------------------------------------------

    /**
     * Compute the Bonat discount lines for `order` under `rule`, WITHOUT
     * touching the order. Returns base lines as produced by
     * accountTaxHelpers.prepare_global_discount_lines() — one per tax group,
     * each with price_unit / quantity / tax_ids and manual tax amounts — the
     * same engine core pos_discount uses for its Global Discount button.
     *
     * WHY: the previous build summed line.priceExcl (tax-excluded) and put the
     * result on a line whose price Odoo treats as tax-INCLUDED (Saudi 15%
     * price-included VAT). A 100% code on a 12.00 SR item produced a -10.43 SR
     * line and left 1.57 SR to pay. Here the amounts come out of the tax engine
     * itself, so 100% is exactly 0.00 and the VAT on the discount line matches
     * the VAT of the lines it discounts, group by group.
     *
     * Amount semantics (all tax-INCLUDED, i.e. what the customer sees):
     *   percentage  -> `amount`% of the order total, capped at `cap` when cap > 0
     *   fixed       -> `amount` off the order total, never below zero
     */
    computeBonatDiscountBaseLines(order, rule) {
        return this.computeBonatOrderDiscount(order, rule).baseLines;
    },

    /**
     * Same as computeBonatDiscountBaseLines(), for ONE rule or SEVERAL (one per
     * whole-order code). Several rules are combined into a single discount —
     * one line per tax group, exactly like a single code — whose amount is the
     * sum of what each code takes off, applied one after the other
     * (bonat_codes.js -> splitBonatOrderDiscount). `amounts[i]` is what code i
     * took off, for the cashier's notes; it does not change the lines.
     *
     * @returns {{ baseLines: Array, amounts: number[] }}
     */
    computeBonatOrderDiscount(order, rules) {
        const empty = { baseLines: [], amounts: [] };
        const ruleList = (Array.isArray(rules) ? rules : [rules]).filter(Boolean);
        const product = this.bonat?.discountProduct;
        if (!product || !ruleList.length || !order) {
            return empty;
        }
        const lines = order
            .getOrderlines()
            .filter((line) => !line.isBonatDiscountLine && !line.isTipLine?.());
        if (!lines.length) {
            return empty;
        }
        const company = order.company;
        const baseLines = lines.map((line) => line.getBaseLine());
        accountTaxHelpers.add_tax_details_in_base_lines(baseLines, company);
        accountTaxHelpers.round_base_lines_tax_details(baseLines, company);

        const total = baseLines.reduce(
            (sum, baseLine) => sum + (baseLine.tax_details?.total_included_currency || 0),
            0
        );
        const rounding = order.currency?.rounding || 0.01;
        if (!(total > rounding / 2)) {
            return empty;
        }

        let amountType;
        let amount;
        const split = splitBonatOrderDiscount(total, ruleList, rounding);
        if (ruleList.length === 1 && ruleList[0].is_percentage) {
            // One percentage code: let Odoo's engine take the percentage itself
            // (unchanged from the single-code build), unless the cap applies.
            const rule = ruleList[0];
            const percent = Math.min(Math.max(Number(rule.amount) || 0, 0), 100);
            const cap = Number(rule.cap) || 0;
            if (cap > 0 && (total * percent) / 100 > cap) {
                amountType = "fixed";
                amount = cap;
            } else {
                amountType = "percent";
                amount = percent;
            }
        } else {
            amountType = "fixed";
            amount = split.total;
        }
        // Never discount more than the order is worth; 100% keeps it exact.
        if ((amountType === "fixed" && amount >= total - rounding / 2) || split.isFull) {
            amountType = "percent";
            amount = 100;
        }
        if (!(amount > 0)) {
            return { baseLines: [], amounts: split.amounts };
        }

        const discountLines = accountTaxHelpers.prepare_global_discount_lines(
            baseLines,
            company,
            amountType,
            amount,
            {
                computation_key: "bonat_discount",
                grouping_function: () => ({
                    grouping_key: { product_id: product },
                    raw_grouping_key: { product_id: product.id },
                }),
            }
        );
        return { baseLines: discountLines, amounts: split.amounts };
    },

    /**
     * Write computed discount base lines onto the order.
     *
     * fresh=false (recompute): existing Bonat lines are updated in place,
     *   matched by tax group; only real changes are written, so repeated
     *   recomputes are no-ops and cannot ping-pong with other listeners.
     * fresh=true (new code): new lines are always created and returned, the
     *   existing ones are left alone so the caller can roll back cleanly.
     *
     * @returns {Promise<Array>} the lines created by this call
     */
    async writeBonatDiscountLines(order, discountBaseLines, { fresh = false } = {}) {
        const product = this.bonat?.discountProduct;
        const created = [];
        if (!product) {
            return created;
        }
        const existingByKey = {};
        const leftovers = [];
        if (!fresh) {
            for (const line of order.bonatDiscountLines) {
                const key = bonatTaxKey(line.tax_ids);
                if (existingByKey[key]) {
                    leftovers.push(line);
                } else {
                    existingByKey[key] = line;
                }
            }
        }

        for (const baseLine of discountBaseLines) {
            const extraTaxData = accountTaxHelpers.export_base_line_extra_tax_data(baseLine);
            const key = bonatTaxKey(baseLine.tax_ids);
            const existing = existingByKey[key];
            if (existing) {
                delete existingByKey[key];
                const priceChanged =
                    Math.abs((existing.price_unit || 0) - baseLine.price_unit) > BONAT_EPSILON;
                const qtyChanged = Math.abs((existing.qty || 0) - baseLine.quantity) > BONAT_EPSILON;
                const extraChanged =
                    JSON.stringify(existing.extra_tax_data || {}) !== JSON.stringify(extraTaxData);
                if (extraChanged) {
                    existing.extra_tax_data = extraTaxData;
                }
                if (priceChanged) {
                    existing.price_unit = baseLine.price_unit;
                }
                if (qtyChanged) {
                    existing.qty = baseLine.quantity;
                }
                continue;
            }
            const line = await this.addLineToOrder(
                {
                    product_id: product,
                    product_tmpl_id: product.product_tmpl_id,
                    price_unit: baseLine.price_unit,
                    qty: baseLine.quantity,
                    tax_ids: [["link", ...baseLine.tax_ids]],
                    extra_tax_data: extraTaxData,
                },
                order,
                { force: true, merge: false },
                false
            );
            if (line) {
                line._bonat_qty_locked = true;
                created.push(line);
            }
        }

        for (const line of [...Object.values(existingByKey), ...leftovers]) {
            line.delete();
        }
        return created;
    },

    /** Recompute the Bonat discount of one order right now. */
    async recomputeBonatDiscount(order) {
        if (
            !order ||
            order.state !== "draft" ||
            order.isRefund ||
            order._bonatCodeInFlight ||
            order._bonatRecomputing ||
            !this.bonat?.discountProduct
        ) {
            return;
        }
        this.reconcileBonatProductCodes(order);
        const rules = order.get_bonat_order_rules?.() || [];
        if (!rules.length) {
            // No whole-order code known here: leave any discount line alone
            // (removing the last code deletes its lines in removeBonatCodes).
            return;
        }
        order._bonatRecomputing = true;
        const selectedUuid = order.uiState?.selected_orderline_uuid;
        try {
            const { baseLines, amounts } = this.computeBonatOrderDiscount(order, rules);
            this._bonatStoreOrderAmounts(order, amounts);
            await this.writeBonatDiscountLines(order, baseLines);
        } catch (error) {
            // A failed recompute leaves the previous discount in place; it must
            // never surface as a blocking error to the cashier.
            console.error("[bonat] discount recompute failed:", error);
        } finally {
            order._bonatRecomputing = false;
            // addLineToOrder selects what it creates; give the cashier back
            // the line they were working on.
            const previous = selectedUuid && order.lines.find((l) => l.uuid === selectedUuid);
            if (previous && order.getSelectedOrderline() !== previous) {
                order.selectOrderline(previous);
            }
        }
    },

    /** Coalesce bursts of order changes into one recompute per order. */
    scheduleBonatDiscountRecompute(order) {
        if (!order || order._bonatRecomputing || !order.get_bonat_codes?.().length) {
            return;
        }
        this._bonatPendingRecompute ||= new Map();
        if (this._bonatPendingRecompute.has(order.uuid)) {
            return;
        }
        const timer = setTimeout(() => {
            this._bonatPendingRecompute.delete(order.uuid);
            this.recomputeBonatDiscount(order);
        }, 30);
        this._bonatPendingRecompute.set(order.uuid, timer);
    },

    /** Run any pending recompute for `order` immediately (used before Validate). */
    async flushBonatDiscountRecompute(order) {
        const timer = this._bonatPendingRecompute?.get(order?.uuid);
        if (timer) {
            clearTimeout(timer);
            this._bonatPendingRecompute.delete(order.uuid);
        }
        await this.recomputeBonatDiscount(order);
    },

    /** Remember what each whole-order code took off (shown on the discount line). */
    _bonatStoreOrderAmounts(order, amounts) {
        const entries = order
            .get_bonat_codes()
            .filter((entry) => entry.kind === "order" && entry.state !== "failed");
        entries.forEach((entry, index) => {
            const value = amounts[index] || 0;
            if (entry.appliedAmount !== value) {
                entry.appliedAmount = value;
            }
        });
    },

    /**
     * A per-product code lives as long as one of its lines does: when the
     * cashier deletes every line of the code, the code leaves the order too.
     * Still unused with Bonat -> nothing is lost. Already used -> say so.
     */
    reconcileBonatProductCodes(order) {
        if (!order?.get_bonat_codes || order._bonatCodeInFlight) {
            return;
        }
        for (const entry of [...order.get_bonat_codes()]) {
            if (entry.kind !== "product" || entry.legacy) {
                continue;
            }
            if (order.bonatLinesOfCode(entry.code).length) {
                continue;
            }
            order.remove_bonat_code(entry.code);
            if (entry.state === "redeemed") {
                bonatWarn(
                    _t(
                        "Bonat code %s was already used with Bonat; removing its items does not give the code back.",
                        entry.code
                    )
                );
            }
        }
    },

    /**
     * Remove codes from an order together with what they gave: the discount
     * of a whole-order code (recomputed without it), the lines of a
     * per-product code, the amount of a voucher.
     */
    async removeBonatCodes(order, codes) {
        for (const code of codes) {
            const entry = order.find_bonat_code(code);
            if (!entry) {
                continue;
            }
            if (entry.kind === "product") {
                for (const line of order.bonatLinesOfCode(entry.code)) {
                    order.removeOrderline(line);
                }
            }
            order.remove_bonat_code(entry.code);
        }
        this._bonatTrimVoucherPayment(order);
        if (!order.get_bonat_order_rules().length) {
            for (const line of order.bonatDiscountLines) {
                order.removeOrderline(line);
            }
            return;
        }
        await this.recomputeBonatDiscount(order);
    },

    /** Keep the voucher payment line within what the remaining vouchers are worth. */
    _bonatTrimVoucherPayment(order) {
        const methodId = this.bonat?.voucherPaymentMethodId;
        if (!methodId) {
            return;
        }
        const worth = order.get_bonat_voucher_amount();
        for (const line of [...(order.payment_ids || [])]) {
            if (line.payment_method_id?.id !== methodId) {
                continue;
            }
            if (!(worth > 0)) {
                order.removePaymentline(line);
            } else if (line.getAmount?.() > worth) {
                line.setAmount(worth);
            }
        }
    },

    // ------------------------------------------------------------------
    // Using the codes with Bonat (redeem)
    // ------------------------------------------------------------------

    /**
     * Use every code of `order` that is not used yet, all in parallel.
     * Never throws; the outcome is written on each code entry.
     * Two calls for the same order share the same requests.
     *
     * @returns {Promise<Array>} the entries that were attempted
     */
    redeemBonatCodes(order) {
        if (!order || !this.bonatApi) {
            return Promise.resolve([]);
        }
        this._bonatRedeemRuns ||= new Map();
        const running = this._bonatRedeemRuns.get(order.uuid);
        if (running) {
            // A run is in flight: wait for it, then pick up anything added since.
            return running.then(() =>
                bonatCodesToRedeem(order.get_bonat_codes()).length
                    ? this.redeemBonatCodes(order)
                    : []
            );
        }
        const entries = bonatCodesToRedeem(order.get_bonat_codes());
        if (!entries.length) {
            return Promise.resolve([]);
        }
        const run = Promise.all(entries.map((entry) => this._redeemBonatEntry(order, entry)))
            .then(() => {
                this._bonatPushCodes(order);
                return entries;
            })
            .finally(() => this._bonatRedeemRuns.delete(order.uuid));
        this._bonatRedeemRuns.set(order.uuid, run);
        return run;
    },

    /**
     * Write the code list straight to the saved order, so another device
     * opening the table right away already sees which codes were used —
     * without waiting for this device's next sync, and without a sync that
     * could collide with a payment in progress.
     */
    _bonatPushCodes(order) {
        const orm = this.env?.services?.orm;
        if (!orm || !this.bonat?.ordersHaveCodesField || typeof order.id !== "number" || order.state !== "draft" || !order.bonatCodesRaw) {
            return;
        }
        orm.silent
            .write("pos.order", [order.id], { x_bonat_codes: order.bonatCodesRaw })
            .catch((error) => console.warn("[bonat] could not save the code states:", error));
    },

    /**
     * Before using codes at payment, take the latest list saved on the order:
     * another device may already have used them when it sent the order to
     * the kitchen. One read, only when unused codes remain.
     */
    async refreshBonatCodesFromServer(order) {
        const orm = this.env?.services?.orm;
        if (!orm || !this.bonat?.ordersHaveCodesField || typeof order.id !== "number") {
            return;
        }
        try {
            const [row] = await orm.silent.read("pos.order", [order.id], ["x_bonat_codes"]);
            order._bonatAdoptRaw?.(row?.x_bonat_codes);
        } catch (error) {
            console.warn("[bonat] could not refresh the code states:", error);
        }
    },

    async _redeemBonatEntry(order, entry) {
        entry.state = "redeeming";
        const now = new Date();
        const redeemData = {
            reward_code: entry.code,
            merchant_id: order.get_bonat_merchant_id(),
            branch_id: this.config.name,
            date: now.toISOString(),
            timestamp: Math.floor(now.getTime() / 1000),
            method: entry.kind === "voucher" ? "Voucher" : "Discount",
        };
        if (entry.kind === "voucher") {
            redeemData.payment_amount = Number(entry.amount) || 0;
            redeemData.payment_method = "BonatGiftCard";
        }
        let result;
        try {
            result = await this.bonatApi.redeem(redeemData);
        } catch (error) {
            result = { success: false, retriable: true, error: String(error?.message || error) };
        }
        applyBonatRedeemResult(entry, result);
        order.save_bonat_codes?.();
        console.log(
            `[bonat] redeem ${entry.code}:`,
            entry.state,
            entry.error ? `(${entry.error})` : ""
        );
        return entry;
    },

    /**
     * Kitchen hook: use the order's codes in the background. The cashier is
     * never kept waiting; a code Bonat refuses is reported with a warning and
     * blocks the payment until it is removed (bonat_validation.js).
     */
    redeemBonatCodesInBackground(order) {
        if (!this.bonat?.enabled || !order?.get_bonat_codes) {
            return;
        }
        if (!bonatCodesToRedeem(order.get_bonat_codes()).length) {
            return;
        }
        this.redeemBonatCodes(order)
            .then((entries) => {
                const refused = entries.filter((entry) => entry.state === "failed");
                for (const entry of refused) {
                    bonatWarn(
                        entry.retriable
                            ? _t(
                                  "Bonat could not be reached to use code %s. It will be tried again at payment.",
                                  entry.code
                              )
                            : _t(
                                  "Bonat refused code %(code)s: %(error)s. Remove the code before payment.",
                                  { code: entry.code, error: entry.error || "-" }
                              )
                    );
                }
            })
            .catch((error) => console.error("[bonat] background redeem failed:", error));
    },

    async sendOrderInPreparation(order, opts = {}) {
        const result = await super.sendOrderInPreparation(...arguments);
        this.redeemBonatCodesInBackground(order);
        return result;
    },

    async sendOrderInPreparationUpdateLastChange(order, opts = {}) {
        const result = await super.sendOrderInPreparationUpdateLastChange(...arguments);
        this.redeemBonatCodesInBackground(order);
        return result;
    },

    /**
     * Keep the discount right when the cashier changes the order after the
     * code was applied (adds/removes items, changes quantities, preset or
     * pricelist) — the same listening pattern core pos_discount uses. Without
     * it, removing an item after a 100% code left a negative total.
     */
    _bonatRegisterDiscountListeners() {
        if (this._bonatListenersRegistered) {
            return;
        }
        this._bonatListenersRegistered = true;
        this.models["pos.order.line"].addEventListener("update", ({ id }) => {
            const line = this.models["pos.order.line"].get(id);
            if (line && !line.isBonatDiscountLine && line.order_id) {
                this.scheduleBonatDiscountRecompute(line.order_id);
            }
        });
        this.models["pos.order"].addEventListener("update", ({ id, fields }) => {
            if (
                fields?.some((field) =>
                    [
                        "lines",
                        "pricelist_id",
                        "fiscal_position_id",
                        "preset_id",
                        "x_bonat_codes",
                    ].includes(field)
                )
            ) {
                this.scheduleBonatDiscountRecompute(this.models["pos.order"].get(id));
            }
        });
    },

    /**
     * Copy the company's Bonat configuration into the runtime-created backup
     * parameter, so the next field wipe (module re-import, platform registry
     * churn) is recoverable. Skips the write when the backup already matches.
     */
    async mirrorBonatConfig() {
        const values = {
            x_bonat_enabled: this.bonat.enabled,
            x_bonat_api_key: this.bonat.apiKey,
            x_bonat_merchant_id: this.bonat.merchantId,
            x_bonat_merchant_name: this.bonat.merchantName,
            x_bonat_demo_mode: this.bonat.demoMode,
        };
        const serialized = JSON.stringify(values);
        const current = await this.data.call("ir.config_parameter", "get_param", [
            BONAT_BACKUP_PARAM,
            "",
        ]);
        if (current === serialized) {
            return;
        }
        await this.data.call("ir.config_parameter", "set_param", [
            BONAT_BACKUP_PARAM,
            serialized,
        ]);
        console.log("[bonat] configuration backup refreshed");
    },

    /**
     * Restore the company's Bonat fields from the backup parameter after a
     * wipe, then reload the configuration so this session comes up configured.
     */
    async healBonatConfigFromBackup() {
        const raw = await this.data.call("ir.config_parameter", "get_param", [
            BONAT_BACKUP_PARAM,
            "",
        ]);
        let backup = null;
        try {
            backup = raw ? JSON.parse(raw) : null;
        } catch {
            backup = null;
        }
        if (!backup || typeof backup !== "object") {
            return;
        }
        const values = {};
        for (const field of BONAT_BACKUP_FIELDS) {
            if (field in backup) {
                values[field] = backup[field];
            }
        }
        if (!Object.keys(values).length) {
            return;
        }
        const companyId = this.config?.company_id?.id ?? this.company?.id;
        await this.data.call("res.company", "write", [[companyId], values]);
        console.warn(
            "[bonat] configuration was empty and has been restored from backup" +
                " (a module update most likely reset it)"
        );
        await this.loadBonatConfig();
    },

    /**
     * Push the catalogue this POS just loaded to Bonat, replacing the
     * webhook-triggered pull that the on-premise module serves through its
     * Python /api/pos/* controllers (impossible on Odoo Online).
     *
     * Runs on every POS open, but a fingerprint check makes the unchanged case
     * free: the hash of the last payload Bonat accepted is kept in localStorage
     * and nothing is sent while it matches. On failure the old hash is kept, so
     * the next POS open retries automatically — no queues, no timers.
     */
    async syncBonatMenu() {
        if (!this.bonat.enabled || !this.bonat.apiKey || !this.bonat.merchantId) {
            return;
        }
        const payload = this.buildBonatMenuPayload();
        if (!payload.products.length) {
            // Bonat's endpoint rejects empty snapshots by design (a broken client
            // must not wipe a merchant's menu), so don't bother sending one.
            console.log("[bonat] no POS products loaded, menu sync skipped");
            return;
        }

        const hash = await bonatCatalogueHash(payload);
        const storageKey = `bonat_menu_hash_${this.bonat.merchantId}_${this.config?.id ?? 0}`;
        let lastHash = null;
        try {
            lastHash = window.localStorage.getItem(storageKey);
        } catch {
            // Storage can be unavailable (private mode); worst case is one
            // redundant idempotent push per POS open.
        }
        if (lastHash === hash) {
            console.log("[bonat] menu unchanged since last push, sync skipped");
            return;
        }

        const result = await this.bonatApi.menuSync({ ...payload, catalogue_hash: hash });
        if (result.success) {
            try {
                window.localStorage.setItem(storageKey, hash);
            } catch {
                // Same storage caveat as above.
            }
            console.log(
                `[bonat] menu sync pushed ${payload.products.length} products and ` +
                    `${payload.categories.length} categories` +
                    (this.bonat.demoMode ? " (Demo Mode, staging)" : "")
            );
        } else {
            // Old hash kept on purpose: the next POS open retries.
            console.error("[bonat] menu sync rejected:", result.error);
        }
    },

    /**
     * Snapshot of the catalogue in the shape Bonat's odooPartnerMenuMapper and
     * odooPartnerCategoryMapper consume. Everything here is already in the POS
     * registry — no extra reads. The Bonat discount line product is excluded:
     * it is plugin plumbing, not a menu item.
     */
    buildBonatMenuPayload() {
        // ADR 0003 on main (docs/adr/0003-odoo19-menu-sync-mode-toggle.md — see the
        // note in data/bonat_company_fields.xml about this branch's own, different
        // ADR 0003): 'template' pushes product.template rows so redemption's
        // allowed_products IDs resolve against templates; 'variant' (default) is
        // today's unchanged product.product behavior.
        const isTemplateMode = this.bonat.menuSyncMode === "template";
        const productModelName = isTemplateMode ? "product.template" : "product.product";
        // this.bonat.discountProduct is always a product.product record (loaded by
        // loadBonatDiscountProduct). In template mode the iterated ids are template
        // ids, a separate sequence (ADR 0003 on main, Decision 4) — compare against
        // the discount product's own template id instead, or the exclusion below
        // would silently compare unrelated id spaces. available_in_pos usually
        // excludes it too, but NOT always: README's "Can this build run on a
        // self-hosted 19.0 instance?" documents setting available_in_pos=True on the
        // discount product as the workaround for the missing
        // pos.data.loadProductFromPos() there — in that configuration this ID check
        // is the ONLY guard excluding the discount product from the synced catalog,
        // not defense in depth on top of available_in_pos.
        const discountProductId = isTemplateMode
            ? this.bonat.discountProduct?.product_tmpl_id?.id
            : this.bonat.discountProduct?.id;
        const products = (this.models[productModelName]?.getAll?.() || [])
            .filter(
                (product) =>
                    product.available_in_pos !== false && product.id !== discountProductId
            )
            .map((product) => ({
                id: product.id,
                name: product.display_name || product.name || "",
                // product.template has no lst_price field (Challenge F2 on the
                // classic build's BON-2420): its list_price already is the
                // effective price (no variant price_extra to add), so mirror it
                // under the key Bonat's odooPartnerMenuMapper actually reads.
                lst_price: isTemplateMode
                    ? product.list_price || 0
                    : typeof product.lst_price === "number"
                      ? product.lst_price
                      : product.list_price || 0,
                available_in_pos: true,
                pos_categ_ids: (product.pos_categ_ids || []).map((categ) => ({ id: categ.id })),
            }))
            // Local-only records carry non-numeric ids; Bonat can't key on those.
            .filter((product) => Number.isInteger(product.id) && product.name);

        const categories = (this.models["pos.category"]?.getAll?.() || [])
            .map((categ) => ({ id: categ.id, name: categ.name || "" }))
            .filter((categ) => Number.isInteger(categ.id) && categ.name);

        return {
            merchant_id: this.bonat.merchantId,
            source: "odoo_online_pos",
            demo: this.bonat.demoMode,
            categories,
            products,
        };
    },

    /** Read the Bonat configuration off res.company and pos.config. */
    async loadBonatConfig() {
        const companyId = this.config?.company_id?.id ?? this.company?.id;
        if (!companyId) {
            console.warn("[bonat] no company id available, integration disabled");
            return;
        }

        const rows = await this.data.read(
            "res.company",
            [companyId],
            [
                "x_bonat_enabled",
                "x_bonat_api_key",
                "x_bonat_merchant_id",
                "x_bonat_merchant_name",
                "x_bonat_demo_mode",
                "x_bonat_menu_sync_mode",
                "x_bonat_api_url",
                "x_bonat_api_url_staging",
            ]
        );
        const row = Array.isArray(rows) ? rows[0] : rows;
        if (!row) {
            console.warn("[bonat] company read returned nothing, integration disabled");
            return;
        }

        // Odoo returns false for empty char fields; normalize to "".
        const str = (value) => (typeof value === "string" ? value : "");

        Object.assign(this.bonat, {
            enabled: !!row.x_bonat_enabled,
            apiKey: str(row.x_bonat_api_key),
            merchantId: str(row.x_bonat_merchant_id),
            merchantName: str(row.x_bonat_merchant_name),
            demoMode: !!row.x_bonat_demo_mode,
            // No DB-level default is possible on this manual column (see the
            // comment in data/bonat_company_fields.xml) — anything but the
            // literal "template" is treated as variant-level, so an unset
            // (NULL) column reads exactly like an explicit "variant".
            menuSyncMode: row.x_bonat_menu_sync_mode === "template" ? "template" : "variant",
        });

        const params = await this.loadBonatUrlParameters();
        this.bonat.apiUrl =
            params[PARAM_API_URL] || str(row.x_bonat_api_url) || BONAT_API_URL_DEFAULT;
        this.bonat.apiUrlStaging =
            params[PARAM_API_URL_STAGING] ||
            str(row.x_bonat_api_url_staging) ||
            BONAT_API_URL_STAGING_DEFAULT;

        if (this.bonat.demoMode) {
            // INFO, not warn: this is a normal operating mode and the POS already
            // shows a banner. Grep "[bonat] Demo Mode" to audit a session.
            console.log(
                "[bonat] Demo Mode is ON, outbound calls go to the staging environment." +
                    " No real loyalty transactions will be recorded."
            );
        }

        if (this.bonat.enabled) {
            // Independent reads: run them side by side to shorten POS opening.
            await Promise.all([
                this.loadBonatDiscountProduct(),
                this.loadBonatVoucherPaymentMethod(),
            ]);
        }
    },

    /**
     * Best-effort read of the two ir.config_parameter overrides.
     *
     * On-premise these are authoritative, read with sudo() inside
     * res.company._get_bonat_api_base_url(). Here the POS client runs as the
     * cashier, who normally cannot read ir.config_parameter, so an AccessError is
     * the expected outcome rather than a fault. Returns {} in that case and the
     * caller falls back to the company field.
     */
    async loadBonatUrlParameters() {
        try {
            const rows = await this.data.searchRead(
                "ir.config_parameter",
                [["key", "in", [PARAM_API_URL, PARAM_API_URL_STAGING]]],
                ["key", "value"]
            );
            return Object.fromEntries(
                (rows || [])
                    .filter((r) => typeof r.value === "string" && r.value)
                    .map((r) => [r.key, r.value])
            );
        } catch {
            // Expected for a non-admin cashier. Not logged at error level.
            return {};
        }
    },

    /**
     * Resolve pos.config.x_bonat_discount_product_id and make sure the product
     * exists in the POS registry, so promo_code_button.js can hand a real
     * product.product record to addLineToOrder() for type=1 codes.
     */
    async loadBonatDiscountProduct() {
        let configRow;
        try {
            const rows = await this.data.read(
                "pos.config",
                [this.config.id],
                ["x_bonat_discount_product_id"]
            );
            configRow = Array.isArray(rows) ? rows[0] : rows;
        } catch (error) {
            console.error("[bonat] could not read the discount product setting:", error);
            return;
        }

        const raw = configRow?.x_bonat_discount_product_id;
        // A many2one read comes back as [id, display_name], or false when unset.
        const productId = (Array.isArray(raw) ? raw[0] : raw?.id ?? raw) || null;
        if (!productId) {
            // Not an error: only type=1 codes need it, and the POS shows a clear
            // dialog if one arrives while this is unset.
            console.log("[bonat] no discount product configured on this POS config");
            return;
        }

        // Already in the registry, e.g. because it is available_in_pos anyway.
        let product = this.models["product.product"].get(productId);
        if (product) {
            this.bonat.discountProduct = product;
            return;
        }

        try {
            const detail = await this.data.read("product.product", [productId], [
                "product_tmpl_id",
            ]);
            const tmplRaw = (Array.isArray(detail) ? detail[0] : detail)?.product_tmpl_id;
            // data.read() returns model records on 19.0 (product_tmpl_id is a
            // record, not an id); raw RPC rows give [id, name] or an id.
            const tmplId = Array.isArray(tmplRaw) ? tmplRaw[0] : tmplRaw?.id ?? tmplRaw;
            if (!tmplId) {
                console.error("[bonat] discount product", productId, "has no template");
                return;
            }
            /*
             * loadProductFromPos() is the standard entry point on saas~19.4, but it
             * does NOT exist on the 19.0 series: verified against
             * odoo19.bonat.io (19.0-20260630), whose data service offers read,
             * searchRead, call, silentCall and callRelated only.
             *
             * Rather than guess at a replacement, fail loudly with the fix. On 19.0
             * the workaround is to tick "Available in POS" on the Bonat discount
             * product: it is then part of the normal POS load and the fast path
             * above resolves it without needing this branch at all. The only cost
             * is that the product becomes visible in the cashier's grid.
             */
            if (typeof this.data.loadProductFromPos === "function") {
                // saas~19.x: loads the template, its variants and relations.
                await this.data.loadProductFromPos([tmplId]);
            } else {
                // 19.0 (Odoo.sh / on-premise): the same server entry point core
                // uses for its own on-demand product loads (loadNewProducts and
                // data_service's missing-records path). It has no
                // available_in_pos filter, so the discount product does NOT
                // need to be visible in the cashier's grid.
                await this.data.callRelated(
                    "product.template",
                    "load_product_from_pos",
                    [this.config.id, [["id", "=", tmplId]], 0, 0],
                    {},
                    false
                );
            }
            product = this.models["product.product"].get(productId);
        } catch (error) {
            console.error("[bonat] failed to load the discount product into the POS:", error);
            return;
        }

        if (!product) {
            console.error(
                "[bonat] discount product",
                productId,
                "could not be resolved after loading; type=1 codes will be refused"
            );
            return;
        }
        this.bonat.discountProduct = product;
    },

    /**
     * Resolve pos.config.x_bonat_voucher_payment_method_id and cache its id.
     *
     * Unlike the discount product, payment methods are always part of the
     * standard POS payload (this.config.payment_method_ids), so no on-demand
     * load into the registry is needed here — just remember which one the
     * merchant picked, the same way loadBonatDiscountProduct resolves its
     * own x_ field.
     */
    async loadBonatVoucherPaymentMethod() {
        let configRow;
        try {
            const rows = await this.data.read(
                "pos.config",
                [this.config.id],
                ["x_bonat_voucher_payment_method_id"]
            );
            configRow = Array.isArray(rows) ? rows[0] : rows;
        } catch (error) {
            console.error("[bonat] could not read the voucher payment method setting:", error);
            return;
        }

        const raw = configRow?.x_bonat_voucher_payment_method_id;
        // A many2one read comes back as [id, display_name], or false when unset.
        const methodId = Array.isArray(raw) ? raw[0] : raw || null;
        if (!methodId) {
            // Not an error: voucher payment-line enforcement is optional. Unset
            // means the voucher stays a banner only, same as before this ticket.
            console.log("[bonat] no voucher payment method configured on this POS config");
            return;
        }

        // Must be linked to THIS config's payment_method_ids, not just exist
        // company-wide — otherwise addPaymentline() would attach a line for a
        // method this session's POS was never configured to accept.
        if (!(this.config.payment_method_ids || []).some((pm) => pm.id === methodId)) {
            console.warn(
                "[bonat] configured voucher payment method",
                methodId,
                "is not linked to this POS config; voucher payment-line enforcement disabled"
            );
            return;
        }
        this.bonat.voucherPaymentMethodId = methodId;
    },
});
