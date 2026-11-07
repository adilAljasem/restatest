/** @odoo-module **/

/*
 * Port of const.py.
 *
 * Only the parts the POS client actually needs are carried over. The large
 * product_fields / pos_config_fields / pos_order_fields lists existed solely to
 * serve the /api/pos/* controllers, which are not part of this build. See the
 * "Inbound API" section of README.md.
 */

// Default Bonat API base URLs. Overridable per company; see resolveBaseUrl().
export const BONAT_API_URL_DEFAULT = "https://api.bonat.io";
export const BONAT_API_URL_STAGING_DEFAULT = "https://stg-api.bonat.io";

// ir.config_parameter keys, kept identical to the on-premise module so an
// existing merchant's overrides are still found.
export const PARAM_API_URL = "pos_bonat_loyalty.api_url";
export const PARAM_API_URL_STAGING = "pos_bonat_loyalty.api_url_staging";

/*
 * Bonat Demo Mode: canned reward-code responses handled locally so merchants can
 * demo the full POS flow without seeded codes on Bonat staging (whose type=2
 * rewards would need product IDs that do not exist in each merchant's own Odoo
 * instance). These codes are ONLY accepted when the company has Demo Mode on; in
 * production mode they are forwarded to Bonat like any other code.
 */
export const DEMO_REWARD_CODES = new Set(["1", "2", "3"]);

// Request timeouts, in milliseconds. Mirrors the Python `timeout=` arguments:
// 10s for the three POS calls, 5s for the settings-page connection test.
export const BONAT_TIMEOUT_MS = 10000;
export const BONAT_TEST_TIMEOUT_MS = 5000;

// Endpoint paths, relative to the resolved base URL.
export const EP_REWARD_CHECK = "/odoo_partner/reward-check";
export const EP_REDEEM = "/odoo_partner/redeem";
export const EP_ORDER = "/odoo_partner/order";
export const EP_MENU_SYNC = "/odoo_partner/menu-sync";

/*
 * Settings survival across module re-imports — POS-side copy of the constants
 * in settings/bonat_settings_const.js (cross-bundle imports do not resolve; see
 * the note at the top of this file). Full rationale lives there: Odoo Online
 * recreates imported-module registrations, dropping the manual x_bonat_* fields
 * and their values; a runtime-created ir.config_parameter has no module owner
 * and survives. Never declare this parameter in data/*.xml.
 */
export const BONAT_BACKUP_PARAM = "pos_bonat_loyalty.settings_backup";
export const BONAT_BACKUP_FIELDS = [
    "x_bonat_enabled",
    "x_bonat_api_key",
    "x_bonat_merchant_id",
    "x_bonat_merchant_name",
    "x_bonat_demo_mode",
];

// Menu-sync pushes a full catalogue snapshot, which can be far larger than the
// three POS calls above; give it a proportionally larger timeout.
export const BONAT_MENU_SYNC_TIMEOUT_MS = 30000;
