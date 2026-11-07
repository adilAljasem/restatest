import { browser } from "@web/core/browser/browser";
import { cookie } from "@web/core/browser/cookie";
import { user } from "@web/core/user";
import { session } from "@web/session";

const COOKIE = "color_scheme";
const ONE_YEAR = 60 * 60 * 24 * 365;

export function getThemeSettings() {
    return session.resta_theme || {};
}

/**
 * Odoo Enterprise ships its own dark mode: a per-user setting
 * (res.users.settings.color_scheme) and a "color_scheme" service that keeps
 * the cookie in sync on every load. When it is present we drive that setting
 * instead of the cookie, so the two never fight.
 */
function hasEnterpriseScheme() {
    return "color_scheme" in (user.settings || {});
}

/** Scheme served for this page load ("light" | "dark"). */
export function getCurrentScheme() {
    return getThemeSettings().scheme || (cookie.get(COOKIE) === "dark" ? "dark" : "light");
}

/**
 * Switch color scheme. Odoo serves dark mode as a separate compiled CSS
 * bundle, so the page reloads once.
 */
export async function setScheme(scheme) {
    document.documentElement.classList.add("rs_switching_scheme");
    if (hasEnterpriseScheme()) {
        try {
            await user.setUserSettings("color_scheme", scheme);
        } catch (error) {
            console.warn("Resta theme: could not save color scheme", error);
        }
    }
    cookie.set(COOKIE, scheme, ONE_YEAR);
    browser.location.reload();
}

export function toggleScheme() {
    return setScheme(getCurrentScheme() === "dark" ? "light" : "dark");
}

/**
 * Community only: on first visit with "Follow device" as company default,
 * adopt the device preference once; the cookie then remembers it.
 * (Enterprise's own color_scheme service already does this.)
 */
const GUARD = "rs_scheme_auto_reload";

function resolveSystemScheme() {
    const settings = getThemeSettings();
    if (hasEnterpriseScheme() || !settings.scheme || cookie.get(COOKIE)) {
        return;
    }
    if (settings.default_scheme !== "system") {
        return;
    }
    const prefersDark = browser.matchMedia?.("(prefers-color-scheme: dark)").matches;
    const wanted = prefersDark ? "dark" : "light";
    cookie.set(COOKIE, wanted, ONE_YEAR);
    if (wanted === settings.scheme) {
        return;
    }
    // Reload at most once per tab, never loop.
    let alreadyReloaded = false;
    try {
        alreadyReloaded = browser.sessionStorage.getItem(GUARD) === "1";
        browser.sessionStorage.setItem(GUARD, "1");
    } catch {
        alreadyReloaded = true;
    }
    if (!alreadyReloaded && cookie.get(COOKIE) === wanted) {
        const reload = () => browser.location.reload();
        if (document.readyState === "complete") {
            browser.setTimeout(reload, 0);
        } else {
            window.addEventListener("load", reload, { once: true });
        }
    }
}

try {
    resolveSystemScheme();
} catch (error) {
    console.warn("Resta theme: color scheme detection skipped", error);
}
