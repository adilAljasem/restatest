/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";

/*
 * Settings-page constants and the connection test.
 *
 * Deliberately duplicates the two default URLs and the endpoint path from
 * app/bonat_const.js rather than importing them. The settings page loads
 * web.assets_backend and never loads point_of_sale._assets_pos, so a
 * cross-bundle import would fail to resolve at runtime.
 */

export const BONAT_API_URL_DEFAULT = "https://api.bonat.io";
export const BONAT_API_URL_STAGING_DEFAULT = "https://stg-api.bonat.io";
export const EP_REWARD_CHECK = "/odoo_partner/reward-check";
export const BONAT_TEST_TIMEOUT_MS = 5000;

/*
 * Settings survival across module re-imports.
 *
 * The x_bonat_* fields are manual fields owned by this module's data XML. On
 * Odoo Online the platform recreates imported-module registrations (observed
 * live: ir.module.module rows churned 1531 -> 1533 -> 1534 within a day on a
 * trial database), and each recreation drops and recreates the fields — taking
 * the merchant's saved values, API key included, with them.
 *
 * The values are therefore mirrored into ONE ir.config_parameter, created at
 * runtime via set_param. A parameter created this way has no ir.model.data
 * record, so no module reinstall, re-import, or registry churn can ever
 * garbage-collect it. It must NEVER be declared in data/*.xml — that would
 * hand its ownership to the module and defeat the whole point.
 *
 * ir.config_parameter is admin-readable only, which is acceptable: the same
 * values live on res.company where the POS client already reads them.
 */
export const BONAT_BACKUP_PARAM = "pos_bonat_loyalty.settings_backup";

/** The company fields covered by the backup, in one place. */
export const BONAT_BACKUP_FIELDS = [
    "x_bonat_enabled",
    "x_bonat_api_key",
    "x_bonat_merchant_id",
    "x_bonat_merchant_name",
    "x_bonat_demo_mode",
];

/**
 * Parse a backup parameter value. Returns null when absent or unreadable so
 * callers can treat "no backup" and "corrupt backup" identically.
 */
export function parseBonatBackup(raw) {
    if (typeof raw !== "string" || !raw) {
        return null;
    }
    try {
        const values = JSON.parse(raw);
        return values && typeof values === "object" ? values : null;
    } catch {
        return null;
    }
}

/**
 * Port of res.config.settings.action_bonat_test_connection().
 *
 * Uses /odoo_partner/reward-check with a dummy reward code as a health-check
 * proxy, since Bonat has no dedicated /ping. The check cares about reachability
 * and authentication, not about the dummy code's validity:
 *
 *   HTTP 200          -> Connected. Bonat accepted the API key, whatever the
 *                        inner `code` value says.
 *   HTTP 401 / 403    -> Failed. The API key was rejected.
 *   HTTP 5xx          -> Failed. Bonat is erroring.
 *   timeout / network -> Failed. Bonat unreachable, or CORS is not configured.
 *   any other 4xx     -> Connected. The key was accepted and the dummy code was
 *                        simply rejected by validation.
 *
 * @returns {{success: Boolean, message: String}}
 */
export async function testBonatConnection({ apiKey, merchantId, demoMode, apiUrl, apiUrlStaging }) {
    if (!apiKey) {
        return {
            success: false,
            message: _t("Connection failed — no API key configured. Enter your API key first."),
        };
    }

    const base = demoMode
        ? apiUrlStaging || BONAT_API_URL_STAGING_DEFAULT
        : apiUrl || BONAT_API_URL_DEFAULT;
    const url = base.replace(/\/+$/, "") + EP_REWARD_CHECK;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), BONAT_TEST_TIMEOUT_MS);

    let response;
    try {
        response = await fetch(url, {
            method: "POST",
            headers: {
                Authorization: `Bearer ${apiKey}`,
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                reward_code: "__bonat_test_connection__",
                merchant_id: merchantId || "",
            }),
            signal: controller.signal,
            credentials: "omit",
            mode: "cors",
        });
    } catch (error) {
        clearTimeout(timer);
        if (error?.name === "AbortError") {
            return {
                success: false,
                message: _t("Connection failed — Bonat did not respond in 5 seconds. Check your network."),
            };
        }
        /*
         * A CORS rejection is indistinguishable from a network failure in the
         * browser: fetch throws an opaque TypeError either way. The message names
         * both causes, because on Odoo Online a missing Access-Control-Allow-Origin
         * for *.odoo.com is the more likely of the two.
         */
        return {
            success: false,
            /*
             * First sentence is byte-identical to the on-premise msgid so its
             * translation still applies. The CORS hint is a separate string,
             * because on Odoo Online a missing Access-Control-Allow-Origin for
             * *.odoo.com is a more likely cause than an actual network fault, and
             * fetch reports both as the same opaque TypeError.
             */
            message:
                _t("Connection failed — could not reach Bonat. Check your network and API URL.") +
                " " +
                _t("If your network is fine, ask Bonat support to allow cross-origin requests from your Odoo domain."),
        };
    }
    clearTimeout(timer);

    if (response.status === 401 || response.status === 403) {
        return {
            success: false,
            message: _t("Connection failed — check your API key. Bonat rejected the credentials."),
        };
    }
    if (response.status >= 500) {
        return {
            success: false,
            message: _t(
                "Connection failed — Bonat is returning a server error (HTTP %s). Try again later.",
                response.status
            ),
        };
    }
    return { success: true, message: _t("Connected to Bonat.") };
}
