/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import {
    BONAT_MENU_SYNC_TIMEOUT_MS,
    BONAT_TIMEOUT_MS,
    DEMO_REWARD_CODES,
    EP_MENU_SYNC,
    EP_ORDER,
    EP_REDEEM,
    EP_REWARD_CHECK,
} from "@pos_bonat_loyalty_online/app/bonat_const";

/*
 * Browser-side Bonat API client.
 *
 * Replaces three Python entry points that the POS used to reach over ORM RPC:
 *
 *   res.company.get_bonat_code_response()      -> rewardCheck()
 *   pos.session.pos_reward_redeem()            -> redeem()
 *   pos.session.pos_order_creation_request()   -> orderCreated()
 *
 * plus res.company._get_bonat_api_base_url() and _bonat_demo_response().
 *
 * The return contract is deliberately identical to the Python one,
 * { success: Boolean, data?: Object, error?: String }, so the calling code in
 * promo_code_button.js and payment_screen.js reads exactly as it does
 * on-premise.
 *
 * TWO THINGS THIS CHANGES, both unavoidable without server-side code:
 *
 * 1. CORS. These requests now originate from the merchant's browser at
 *    https://<db>.odoo.com, not from an Odoo server. The Bonat API must return
 *    Access-Control-Allow-Origin for *.odoo.com and allow the Authorization and
 *    Content-Type request headers on the preflight. Until it does, every call
 *    here fails at the network layer and surfaces as a transport error.
 *
 * 2. Credential exposure. The API key is read from res.company by the POS
 *    client, so it is present in the browser and visible to any cashier who can
 *    open devtools. On-premise it never left the server. Bonat should issue a
 *    POS-scoped public key or a short-lived per-session token; `get authHeader()`
 *    below is the single place that needs to change when it does.
 */
export class BonatApi {
    /**
     * @param {Object} cfg resolved Bonat configuration, as built by
     *      bonat_store.js -> loadBonatConfig(). Read lazily through a getter so
     *      a later reload of the config is picked up without rebuilding this.
     * @param {Function} cfgGetter returns the current cfg object
     * @param {Object} pos the PosStore, used only by the Demo Mode synthesizer
     *      to look up POS-available products.
     */
    constructor(cfgGetter, pos) {
        this._cfgGetter = cfgGetter;
        this.pos = pos;
    }

    get cfg() {
        return this._cfgGetter() || {};
    }

    get enabled() {
        return !!this.cfg.enabled;
    }

    get apiKey() {
        return this.cfg.apiKey || "";
    }

    get merchantId() {
        return this.cfg.merchantId || "";
    }

    get merchantName() {
        return this.cfg.merchantName || "";
    }

    get demoMode() {
        return !!this.cfg.demoMode;
    }

    /**
     * Demo Mode routes to the staging base URL, production to the other. Mirrors
     * res.company._get_bonat_api_base_url(). The URLs themselves are resolved
     * once at POS startup by bonat_store.js.
     */
    get baseUrl() {
        const url = this.demoMode ? this.cfg.apiUrlStaging : this.cfg.apiUrl;
        return (url || "").replace(/\/+$/, "");
    }

    get authHeader() {
        // Single choke point for the credential. Swap to a scoped/ephemeral
        // token here rather than threading a new value through every call site.
        return `Bearer ${this.apiKey}`;
    }

    /**
     * Guard shared by all three calls. Mirrors the
     * "not enable_bonat_integration or not bonat_api_key" check that opens each
     * Python method. Returns an error envelope, or null when configuration is OK.
     */
    _configError() {
        if (!this.enabled || !this.apiKey) {
            return {
                success: false,
                error: _t("Bonat integration is not enabled or API key is missing."),
            };
        }
        return null;
    }

    /**
     * Normalize Bonat's `errors` member. It arrives as a string, a list of
     * strings, or occasionally a dict; AlertDialog requires `body` to be a
     * string, so anything else must be flattened or discarded.
     * Mirrors the repeated `if isinstance(error_message, list)` blocks.
     */
    static _errorText(raw, fallback) {
        if (typeof raw === "string" && raw) {
            return raw;
        }
        if (Array.isArray(raw)) {
            const joined = raw.map((e) => String(e)).join(" ").trim();
            if (joined) {
                return joined;
            }
        }
        return fallback;
    }

    /**
     * POST helper. Resolves to { ok, status, statusText, json, text } and throws
     * only on transport failure, which is the JS analogue of
     * requests.exceptions.RequestException.
     */
    async _post(path, payload, timeoutMs = BONAT_TIMEOUT_MS) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(this.baseUrl + path, {
                method: "POST",
                headers: {
                    Authorization: this.authHeader,
                    "Content-Type": "application/json",
                },
                body: JSON.stringify(payload),
                signal: controller.signal,
                // No credentials: this is a third-party origin and Odoo's session
                // cookie must not be forwarded to it.
                credentials: "omit",
                mode: "cors",
            });
            const text = await response.text();
            let json = null;
            try {
                json = text ? JSON.parse(text) : null;
            } catch {
                json = null;
            }
            return {
                ok: response.status === 200,
                status: response.status,
                statusText: response.statusText,
                json,
                text,
            };
        } finally {
            clearTimeout(timer);
        }
    }

    // ------------------------------------------------------------------
    // res.company.get_bonat_code_response()
    // ------------------------------------------------------------------
    /**
     * Validate a Bonat reward code.
     *
     * @param {String} code
     * @param {Array} products current order lines as
     *      [{ product_id, quantity }]. Accepted for shape compatibility with the
     *      Python signature; only the Demo Mode synthesizer looks at it, and even
     *      there it is unused, exactly as on-premise.
     */
    async rewardCheck(code, products = []) {
        const configError = this._configError();
        if (configError) {
            return configError;
        }

        if (this.demoMode && DEMO_REWARD_CODES.has(code)) {
            return await this.demoResponse(code, products);
        }

        let response;
        try {
            response = await this._post(EP_REWARD_CHECK, {
                reward_code: code,
                merchant_id: this.merchantId,
            });
        } catch (error) {
            console.error("[bonat] reward-check transport error:", error);
            // Python returns a bare {"success": False} here, with no error key,
            // and the JS caller falls back to its own generic message.
            return { success: false };
        }

        if (!response.ok) {
            return {
                success: false,
                error: _t("API Error: %s", response.text || response.status),
            };
        }

        const data = response.json || {};
        console.log("[bonat] reward-check response for code", code, data);

        if (data.code === 0 && data.data) {
            let apiData = data.data;
            // Bonat occasionally returns type=2 without allowed_products, i.e. a
            // coupon with no product restriction. The POS type=2 flow assumes
            // allowed_products.product_id exists and crashes on undefined;
            // semantically an unrestricted type=2 coupon is a whole-order
            // discount, so coerce it to type=1.
            if (apiData.type === 2 && !apiData.allowed_products) {
                console.log(
                    "[bonat] type=2 coupon",
                    code,
                    "has no allowed_products, treating as type=1 (whole-order discount)"
                );
                apiData = { ...apiData, type: 1 };
            }
            return { success: true, data: apiData };
        }

        return {
            success: false,
            error: BonatApi._errorText(
                data.errors,
                _t("This code has already been used. Please try a different one.")
            ),
        };
    }

    // ------------------------------------------------------------------
    // Replaces the /api/pos/products + /api/pos/categories pull API
    // ------------------------------------------------------------------
    /**
     * Push the POS catalogue snapshot to Bonat.
     *
     * On-premise, Bonat's backend pulls the menu through the plugin's Python
     * /api/pos/* controllers when its menu.updated webhook fires. Those routes
     * cannot exist in this importable build, so the client pushes instead. The
     * payload shape mirrors what Bonat's odooPartnerMenuMapper consumes; it is
     * built by bonat_store.js -> buildBonatMenuPayload().
     *
     * In Demo Mode the push goes to the staging base URL like every other call
     * here, so demoing can never touch a production menu.
     *
     * @param {Object} payload { merchant_id, source, demo, catalogue_hash,
     *      categories: [{id, name}], products: [{id, name, lst_price,
     *      available_in_pos, pos_categ_ids: [{id}]}] }
     * @returns {{success: Boolean, error?: String}}
     */
    async menuSync(payload) {
        const configError = this._configError();
        if (configError) {
            return configError;
        }

        let response;
        try {
            response = await this._post(EP_MENU_SYNC, payload, BONAT_MENU_SYNC_TIMEOUT_MS);
        } catch (error) {
            console.error("[bonat] menu-sync transport error:", error);
            return { success: false, error: String(error?.message || error) };
        }

        if (!response.ok) {
            return {
                success: false,
                error: _t("API Error: %s", response.text || response.status),
            };
        }
        return { success: true };
    }

    // ------------------------------------------------------------------
    // res.company._bonat_demo_response()
    // ------------------------------------------------------------------
    /**
     * Synthesize a canned reward-check response for the Demo Mode codes.
     *
     * The shape matches what promo_code_button.js expects exactly, so the POS
     * flow (discount line, per-product popup, banner) runs identically to a real
     * Bonat response. For type=2 codes, allowed_products.product_id is filled
     * from the first few POS-available products so the picker renders; the JS
     * auto-adds selected products to the order, so nothing needs to be in the
     * order beforehand.
     */
    async demoResponse(code, products) {
        console.log("[bonat] Demo Mode: code", code, "handled locally, no network call");

        if (code === "1") {
            return {
                success: true,
                data: { type: 1, is_percentage: true, discount_amount: 10 },
            };
        }

        let demoProducts = [];
        try {
            demoProducts = await this.pos.data.searchRead(
                "product.product",
                [["available_in_pos", "=", true]],
                ["id", "display_name"],
                { limit: 5, order: "name" }
            );
        } catch (error) {
            console.error("[bonat] Demo Mode product lookup failed:", error);
            return {
                success: false,
                error: _t("Could not load demo products. Check your connection and try again."),
            };
        }

        if (!demoProducts.length) {
            return {
                success: false,
                error: _t(
                    "Demo code '%s' needs at least one POS-available product in the catalog.",
                    code
                ),
            };
        }

        // promo_code_button.js compares line.getProduct().id.toString() against
        // allowed_products.product_id. Return string IDs so the discount block
        // matches; otherwise products get added to the order but no discount
        // lands on the line.
        const allowedProductIds = demoProducts.map((p) => String(p.id));

        if (code === "2") {
            return {
                success: true,
                data: {
                    type: 2,
                    is_percentage: true,
                    discount_amount: 10,
                    max_discount_amount: 20,
                    allowed_products: { product_id: allowedProductIds, quantity: 1 },
                },
            };
        }

        if (code === "3") {
            return {
                success: true,
                data: {
                    type: 2,
                    is_percentage: true,
                    discount_amount: 100,
                    max_discount_amount: 999999,
                    allowed_products: { product_id: allowedProductIds, quantity: 2 },
                },
            };
        }

        return { success: false, error: _t("Unknown demo code: %s", code) };
    }

    // ------------------------------------------------------------------
    // pos.session.pos_reward_redeem()
    // ------------------------------------------------------------------
    async redeem(redeemData) {
        console.log("[bonat] redeem request:", redeemData);

        const requiredFields = ["reward_code", "merchant_id", "branch_id", "date", "timestamp"];
        const missing = requiredFields.filter((field) => !redeemData?.[field]);
        if (missing.length) {
            console.error("[bonat] missing required fields in redeem request:", missing);
            return {
                success: false,
                error: _t("Missing required fields: %s", missing.join(", ")),
            };
        }

        if (!this.enabled) {
            console.warn("[bonat] integration is not enabled");
            return { success: false, error: _t("Bonat integration is not enabled.") };
        }
        if (!this.apiKey) {
            console.warn("[bonat] API key is missing");
            return { success: false, error: _t("Bonat API key is missing.") };
        }

        // Demo Mode mirrors the rewardCheck short-circuit. Bonat staging does not
        // know demo codes 1/2/3, so forwarding them to /odoo_partner/redeem
        // returns an error payload that would later fail AlertDialog's string
        // prop validation.
        const rewardCode = redeemData.reward_code;
        if (this.demoMode && DEMO_REWARD_CODES.has(rewardCode)) {
            console.log("[bonat] Demo Mode: redeem code", rewardCode, "handled locally");
            return { success: true, data: { reward_code: rewardCode, demo: true } };
        }

        // method / payment_* follow Bonat's POS Integration Guide v3.7
        // (Redeem Confirmation API); sent only when the caller provides them.
        const body = {
            reward_code: rewardCode,
            merchant_id: redeemData.merchant_id,
            branch_id: redeemData.branch_id,
            date: redeemData.date,
            timestamp: redeemData.timestamp,
        };
        for (const key of ["method", "payment_method", "payment_amount"]) {
            if (redeemData[key] !== undefined && redeemData[key] !== "") {
                body[key] = redeemData[key];
            }
        }

        let response;
        try {
            response = await this._post(EP_REDEEM, body);
        } catch (error) {
            console.error("[bonat] redeem transport error:", error);
            // Network/timeout: the code was not refused, retry later.
            return {
                success: false,
                retriable: true,
                error: _t("Request error: %s", String(error?.message || error)),
            };
        }

        if (!response.ok) {
            console.error("[bonat] redeem API error:", response.text);
            const status = Number(response.status) || 0;
            return {
                success: false,
                // Bonat down or overloaded: not a verdict on the code itself.
                retriable: status === 0 || status === 408 || status === 429 || status >= 500,
                error: _t("API Error: %s - %s", response.status, response.statusText),
            };
        }

        const data = response.json || {};
        console.log("[bonat] redeem response:", data);
        if (data.code === 0) {
            return { success: true, data: data.data };
        }
        const errorText = BonatApi._errorText(data.errors, _t("Invalid reward code."));
        console.warn("[bonat] redeem returned an error:", errorText);
        return { success: false, error: errorText };
    }

    // ------------------------------------------------------------------
    // pos.session.pos_order_creation_request()
    // ------------------------------------------------------------------
    async orderCreated(orderCreationData) {
        const configError = this._configError();
        if (configError) {
            return configError;
        }

        // Demo Mode: no real order should reach Bonat from a demo session,
        // including orders with no Bonat code, since /odoo_partner/order also
        // records the raw transaction on Bonat's side. Skip the call and return
        // the shape payment_screen.js treats as success.
        if (this.demoMode) {
            console.log("[bonat] Demo Mode: order-creation request skipped, no network call");
            return { success: true, data: { demo: true } };
        }

        let response;
        try {
            console.log("[bonat] POS order data:", orderCreationData);
            response = await this._post(EP_ORDER, orderCreationData);
        } catch (error) {
            console.error("[bonat] order-creation transport error:", error);
            // Matches the Python, which returns a bare {"success": False}.
            return { success: false };
        }

        if (!response.ok) {
            return { success: false, error: _t("API Error: %s", response.text) };
        }

        const data = response.json || {};
        if (data.code === 0) {
            return { success: true, data: data.data };
        }
        return {
            success: false,
            error: BonatApi._errorText(data.errors, _t("Invalid code.")),
        };
    }
}
