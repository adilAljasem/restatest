/** Helpers shared by the side menu and the section page (menu hub). */

export const MENU_HUB_TAG = "resta_sidebar_theme.menu_hub";
export const MENU_HUB_PATH = "menu-hub";

export function normalize(str) {
    // Lowercase + strip Arabic diacritics/tatweel and unify alef/yaa/taa forms
    return (str || "")
        .toLowerCase()
        .replace(/[ً-ٟـ]/g, "")
        .replace(/[أإآ]/g, "ا")
        .replace(/ى/g, "ي")
        .replace(/ة/g, "ه");
}

/**
 * Menus under `menu` that open a screen (an action and no children),
 * depth first, each with the names between `menu` and itself.
 */
export function collectLeaves(menu, trail = []) {
    const leaves = [];
    for (const child of menu.childrenTree || []) {
        if (child.childrenTree?.length) {
            leaves.push(...collectLeaves(child, [...trail, child.name]));
        } else if (child.actionID) {
            leaves.push({ menu: child, trail });
        }
    }
    return leaves;
}

/** Link of a menu entry: its action, or the section page when it groups several screens. */
export function menuHref(menu) {
    if (menu.childrenTree?.length) {
        const leaves = collectLeaves(menu);
        if (leaves.length === 1) {
            return menuHref(leaves[0].menu);
        }
        return `/odoo/${menu.id}/${MENU_HUB_PATH}?menu_id=${menu.appID}`;
    }
    if (!menu.actionID) {
        return "#";
    }
    return `/odoo/${menu.actionPath || "action-" + menu.actionID}`;
}

// Small icon for each option card, guessed from the menu's technical id
const ICON_RULES = [
    [/report|analysis|statistic|dashboard|_kpi/, "fa-bar-chart"],
    [/config|setting|settings|_conf/, "fa-sliders"],
    [/payment_method|payment/, "fa-credit-card"],
    [/invoice|bill|refund|credit_note/, "fa-file-text-o"],
    [/tax/, "fa-percent"],
    [/journal|account|ledger|asset|budget|deferred/, "fa-book"],
    [/bank|cash|statement|reconcil/, "fa-university"],
    [/session|shift|attendance|timesheet/, "fa-clock-o"],
    [/employee|department|job|payroll|payslip|contract/, "fa-id-badge"],
    [/customer|partner|contact|vendor|supplier/, "fa-users"],
    [/product|variant|attribute|pricelist|combo|categ/, "fa-cube"],
    [/stock|inventory|warehouse|location|quant|lot|scrap/, "fa-cubes"],
    [/picking|transfer|receipt|delivery|route|move/, "fa-exchange"],
    [/order|sale|purchase|kitchen|kds|table|floor/, "fa-list-alt"],
    [/loyalty|coupon|promo|gift/, "fa-gift"],
    [/user|group|access|security/, "fa-lock"],
    [/mail|message|channel|template/, "fa-envelope-o"],
];

export function menuIcon(menu) {
    const key = (menu.xmlid || "").toLowerCase();
    for (const [re, icon] of ICON_RULES) {
        if (re.test(key)) {
            return icon;
        }
    }
    return "fa-file-o";
}
