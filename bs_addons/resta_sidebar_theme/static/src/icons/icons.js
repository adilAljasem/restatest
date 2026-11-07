import { markup } from "@odoo/owl";

/**
 * Resta line icons — 48×48 grid, stroke-only, round caps/joins, drawn with
 * currentColor so they are black in light mode and white in dark mode.
 * vision / smart / erp / kpi / pay / pos are Resta's own product glyphs
 * (resta.sa); the rest are drawn in the same style.
 */
export const RS_ICONS = {
    // Resta product glyphs
    vision: '<path d="M6.5 24s6.8-10 17.5-10 17.5 10 17.5 10-6.8 10-17.5 10S6.5 24 6.5 24Z"/><circle cx="24" cy="24" r="5.2"/><path d="M24 7v3M24 38v3M7 24H4M44 24h-3"/>',
    smart: '<rect x="17" y="14" width="14" height="20" rx="5"/><circle cx="24" cy="24" r="2.5"/><path d="M13 17a10 10 0 0 0 0 14M35 17a10 10 0 0 1 0 14M8.5 13a16 16 0 0 0 0 22M39.5 13a16 16 0 0 1 0 22"/>',
    erp: '<rect x="8" y="9" width="32" height="9" rx="3"/><rect x="8" y="30" width="32" height="9" rx="3"/><path d="M15 18v12M33 18v12M24 18v12M12 13.5h.1M12 34.5h.1"/>',
    kpi: '<path d="M9 38V25h7v13M20.5 38V17h7v21M32 38V9h7v29M7 38h34"/>',
    pay: '<rect x="7" y="13" width="34" height="25" rx="6"/><path d="M7 21h34M13 31h8M31 27c3 2 3 6 0 8M35 25c5 4 5 10 0 14"/>',
    pos: '<rect x="7" y="8" width="34" height="25" rx="5"/><path d="M18 40h12M24 33v7M31 15h5v9h-5M12 16h13M12 21h9M12 26h12"/>',

    // Same style
    invoice: '<path d="M12 6h18l8 8v28H12Z"/><path d="M30 6v8h8M18 22h12M18 28h12M18 34h7"/>',
    ledger: '<path d="M38 6H14a4 4 0 0 0-4 4v28a4 4 0 0 1 4-4h24Z"/><path d="M10 38a4 4 0 0 0 4 4h24v-8M18 14h12M18 20h8"/>',
    box: '<path d="M24 5 41 14v20L24 43 7 34V14Z"/><path d="m7 14 17 9 17-9M24 23v20M15.5 9.5l17 9"/>',
    cart: '<path d="M5 8h6l4 22h22l4-15H13"/><circle cx="18" cy="39" r="3"/><circle cx="34" cy="39" r="3"/>',
    bag: '<path d="M9 16h30l-2 25H11Z"/><path d="M17 20v-6a7 7 0 0 1 14 0v6"/>',
    target: '<circle cx="22" cy="26" r="15"/><circle cx="22" cy="26" r="7.5"/><path d="M22 26 36 12M33 7v6h6"/>',
    factory: '<path d="M6 41V20l10 6v-6l10 6v-6l10 6V8h6v33Z"/><path d="M13 34h4M21 34h4M29 34h4"/>',
    chat: '<path d="M10 9h28a4 4 0 0 1 4 4v18a4 4 0 0 1-4 4H22l-9 7v-7h-3a4 4 0 0 1-4-4V13a4 4 0 0 1 4-4Z"/><path d="M15 19h18M15 25h11"/>',
    contacts: '<rect x="8" y="6" width="32" height="36" rx="5"/><circle cx="24" cy="20" r="5"/><path d="M15 34c1.5-5 5-7 9-7s7.5 2 9 7"/>',
    apps: '<rect x="8" y="8" width="13" height="13" rx="3.5"/><rect x="27" y="8" width="13" height="13" rx="3.5"/><rect x="8" y="27" width="13" height="13" rx="3.5"/><rect x="27" y="27" width="13" height="13" rx="3.5"/>',
    settings: '<path d="M8 14h18M34 14h6M8 34h6M22 34h18M8 24h8M24 24h16"/><circle cx="30" cy="14" r="4"/><circle cx="18" cy="34" r="4"/><circle cx="20" cy="24" r="4"/>',
    calendar: '<rect x="7" y="10" width="34" height="31" rx="5"/><path d="M7 19h34M16 6v8M32 6v8M15 27h4M22 27h4M29 27h4M15 33h4M22 33h4"/>',
    kanban: '<rect x="6" y="7" width="10" height="26" rx="3"/><rect x="19" y="7" width="10" height="34" rx="3"/><rect x="32" y="7" width="10" height="18" rx="3"/>',
    people: '<circle cx="18" cy="17" r="6"/><path d="M6 39c1.5-7 6-11 12-11s10.5 4 12 11M30 11.5a6 6 0 0 1 0 11M34 28c4 1.5 7 5 8 11"/>',
    clock: '<circle cx="24" cy="24" r="17"/><path d="M24 14v10l7 5"/>',
    sun: '<circle cx="24" cy="24" r="8"/><path d="M24 5v4M24 39v4M5 24h4M39 24h4M10.6 10.6l2.8 2.8M34.6 34.6l2.8 2.8M10.6 37.4l2.8-2.8M34.6 13.4l2.8-2.8"/>',
    recruit: '<circle cx="20" cy="17" r="7"/><path d="M6 40c1.6-7.5 7-12 14-12 3 0 5.6.8 7.8 2.3M37 28v12M31 34h12"/>',
    receipt: '<path d="M11 5h26v38l-4.3-3-4.4 3-4.3-3-4.3 3-4.4-3-4.3 3Z"/><path d="M17 15h14M17 21h14M17 27h8"/>',
    hourglass: '<path d="M13 6h22M13 42h22M15 6c0 9 9 12 9 18s-9 9-9 18M33 6c0 9-9 12-9 18s9 9 9 18"/>',
    globe: '<circle cx="24" cy="24" r="17"/><path d="M7 24h34M24 7c5 5 7 11 7 17s-2 12-7 17c-5-5-7-11-7-17s2-12 7-17Z"/>',
    mail: '<rect x="5" y="10" width="38" height="28" rx="5"/><path d="m7 13 17 13 17-13"/>',
    ticket: '<path d="M6 14a3 3 0 0 1 3-3h30a3 3 0 0 1 3 3v6a4 4 0 0 0 0 8v6a3 3 0 0 1-3 3H9a3 3 0 0 1-3-3v-6a4 4 0 0 0 0-8Z"/><path d="M30 11v4M30 21v6M30 33v4"/>',
    survey: '<rect x="9" y="8" width="30" height="35" rx="5"/><path d="M18 5h12v7H18ZM17 27l5 5 9-10"/>',
    car: '<path d="M8 31v-7l4-10a4 4 0 0 1 3.7-2.5h16.6A4 4 0 0 1 36 14l4 10v7"/><path d="M8 24h32M8 31h3M19 31h10M37 31h3"/><circle cx="15" cy="32" r="4"/><circle cx="33" cy="32" r="4"/>',
    wrench: '<path d="M29.5 6.5a10 10 0 0 0-10 13.3L7.2 32.1a3.8 3.8 0 0 0 5.4 5.4l12.3-12.3a10 10 0 0 0 13.3-10l-5.7 5.7-5.4-1.4-1.4-5.4Z"/>',
    book: '<path d="M24 12c-4-3-9-4-17-4v28c8 0 13 1 17 4 4-3 9-4 17-4V8c-8 0-13 1-17 4ZM24 12v28"/>',
    folder: '<path d="M5 13a4 4 0 0 1 4-4h9l4 5h17a4 4 0 0 1 4 4v19a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4Z"/>',
    sign: '<path d="m30 8 10 10-21 21H9V29Z"/><path d="m25 13 10 10M8 44h32"/>',
    lifebuoy: '<circle cx="24" cy="24" r="17"/><circle cx="24" cy="24" r="7"/><path d="m12 12 7 7M29 29l7 7M36 12l-7 7M19 29l-7 7"/>',
    planning: '<path d="M7 8v32h34"/><rect x="13" y="12" width="16" height="6" rx="3"/><rect x="19" y="21" width="18" height="6" rx="3"/><rect x="13" y="30" width="12" height="6" rx="3"/>',
    todo: '<path d="m8 13 3 3 6-6M8 25l3 3 6-6M8 37l3 3 6-6M23 13h17M23 25h17M23 37h12"/>',
    dining: '<path d="M14 5v12a5 5 0 0 0 10 0V5M19 5v38M34 43V5c-4 3-6 9-6 15v6h6"/>',
    chef: '<path d="M14 28a8 8 0 0 1-1-15.9A11 11 0 0 1 24 5a11 11 0 0 1 11 7.1A8 8 0 0 1 34 28v12H14Z"/><path d="M14 34h20"/>',
    shield: '<path d="M24 5 39 11v11c0 10-6 17-15 21C15 39 9 32 9 22V11Z"/><path d="m17 24 5 5 9-10"/>',
    gift: '<rect x="7" y="16" width="34" height="9" rx="2"/><path d="M10 25v16h28V25M24 16v25M24 16c-3-6-11-8-11-3 0 3 6 3 11 3Zm0 0c3-6 11-8 11-3 0 3-6 3-11 3Z"/>',
    headset: '<path d="M8 28v-4a16 16 0 0 1 32 0v4"/><rect x="6" y="27" width="8" height="12" rx="3"/><rect x="34" y="27" width="8" height="12" rx="3"/><path d="M38 39c0 3-3 5-8 5h-4"/>',
    learn: '<path d="m24 8 20 10-20 10L4 18Z"/><path d="M12 22v10c4 4 20 4 24 0V22M44 18v10"/>',
    megaphone: '<path d="M8 19v10a3 3 0 0 0 3 3h5l17 9V7L16 16h-5a3 3 0 0 0-3 3Z"/><path d="m16 32 3 9M39 18v12"/>',
    barcode: '<path d="M6 12V8h6M36 8h6v4M42 36v4h-6M12 40H6v-4M13 16v16M19 16v16M24 16v16M30 16v16M35 16v16"/>',
    approve: '<circle cx="24" cy="24" r="17"/><path d="m16 24 6 6 11-12"/>',
    sheet: '<rect x="6" y="8" width="36" height="32" rx="5"/><path d="M6 18h36M6 29h36M18 8v32"/>',
    repeat: '<path d="M38 18A15 15 0 0 0 10 16M10 30a15 15 0 0 0 28 2"/><path d="M10 8v8h8M38 40v-8h-8"/>',
};

/** Icon keys selectable per app (kept in sync with ir_ui_menu.RS_ICON_KEYS). */
export const RS_ICON_KEYS = Object.keys(RS_ICONS);

// Full xmlid of the root menu → icon
const BY_XMLID = {
    "base.menu_management": "apps",
    "base.menu_administration": "settings",
    "account.menu_finance": "invoice",
    "account_accountant.menu_accounting": "ledger",
    "mail.menu_root_discuss": "chat",
};
// Module (xmlid prefix, or settings app name) → icon
const BY_MODULE = {
    general_settings: "settings", base_setup: "settings", base: "settings",
    the_dash: "kpi", spreadsheet_dashboard: "kpi", board: "kpi", spreadsheet_edition: "sheet",
    point_of_sale: "pos", pos_restaurant: "dining", pos_preparation_display: "chef",
    pos_self_order: "pos", pos_loyalty: "gift",
    account: "invoice", account_accountant: "ledger", accountant: "ledger",
    payment: "pay", hr_payroll: "pay", account_payment: "pay",
    stock: "box", stock_barcode: "barcode", purchase: "cart", mrp: "factory",
    mrp_plm: "erp", quality_control: "shield", quality: "shield",
    repair: "wrench", maintenance: "wrench", fleet: "car",
    sale: "bag", sale_management: "bag", sale_renting: "repeat",
    sale_subscription: "repeat", crm: "target", loyalty: "gift",
    website: "globe", website_sale: "globe", website_slides: "learn",
    mass_mailing: "mail", marketing_automation: "megaphone", social: "megaphone",
    sms: "chat", whatsapp: "chat", event: "ticket", survey: "survey",
    im_livechat: "headset", helpdesk: "lifebuoy",
    mail: "chat", calendar: "calendar", contacts: "contacts",
    project: "kanban", project_todo: "todo", note: "todo",
    knowledge: "book", documents: "folder", sign: "sign", approvals: "approve",
    planning: "planning", industry_fsm: "kanban",
    hr: "people", hr_attendance: "clock", hr_holidays: "sun",
    hr_recruitment: "recruit", hr_expense: "receipt", hr_timesheet: "hourglass",
    timesheet_grid: "hourglass", hr_appraisal: "kpi", hr_skills: "learn",
    lunch: "dining", iot: "smart", resta_vision: "vision",
};

export function iconKeyFor(xmlidOrModule) {
    const value = xmlidOrModule || "";
    if (BY_XMLID[value]) {
        return BY_XMLID[value];
    }
    const module = value.split(".")[0];
    if (BY_MODULE[module]) {
        return BY_MODULE[module];
    }
    if (module.startsWith("hr_")) {
        return "people";
    }
    if (module.startsWith("pos_")) {
        return "pos";
    }
    if (module.startsWith("stock_")) {
        return "box";
    }
    if (module.startsWith("account_")) {
        return "invoice";
    }
    if (module.startsWith("sale_")) {
        return "bag";
    }
    if (module.startsWith("website_")) {
        return "globe";
    }
    return "erp";
}

const cache = new Map();
/** Inline SVG markup for an icon key, ready for t-out. */
export function iconMarkup(key) {
    const k = RS_ICONS[key] ? key : "erp";
    if (!cache.has(k)) {
        cache.set(
            k,
            markup(
                `<svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="2.8" ` +
                    `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${RS_ICONS[k]}</svg>`
            )
        );
    }
    return cache.get(k);
}
