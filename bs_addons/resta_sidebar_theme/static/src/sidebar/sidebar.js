import { Component, onMounted, onWillUnmount, useExternalListener, useRef, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { localization } from "@web/core/l10n/localization";
import { user } from "@web/core/user";
import { useBus, useService } from "@web/core/utils/hooks";
import { getCurrentScheme, getThemeSettings, toggleScheme } from "../js/theme_scheme";
import { iconKeyFor, iconMarkup } from "../icons/icons";

const LS_COLLAPSED = "rs_sidebar_collapsed";
const LS_OPEN = "rs_sidebar_open";

function lsGet(key) {
    try {
        return browser.localStorage.getItem(key);
    } catch {
        return null;
    }
}
function lsSet(key, value) {
    try {
        browser.localStorage.setItem(key, value);
    } catch {
        // storage unavailable (private mode, blocked): ignore
    }
}

// Automatic grouping of apps when no section is set on the menu.
// key -> [sequence, label]
const SECTIONS = {
    overview: [10, () => _t("Overview")],
    operations: [20, () => _t("Operations")],
    sales: [30, () => _t("Sales & Marketing")],
    finance: [40, () => _t("Finance")],
    people: [50, () => _t("People")],
    workspace: [60, () => _t("Workspace")],
    other: [80, () => _t("Apps")],
    admin: [90, () => _t("Administration")],
};
const MODULE_SECTION = {
    the_dash: "overview", spreadsheet_dashboard: "overview", board: "overview",
    point_of_sale: "operations", pos_restaurant: "operations", stock: "operations",
    mrp: "operations", purchase: "operations", repair: "operations",
    quality_control: "operations", maintenance: "operations", fleet: "operations",
    planning: "operations", field_service: "operations", industry_fsm: "operations",
    sale: "sales", sale_management: "sales", crm: "sales", website: "sales",
    loyalty: "sales", event: "sales", mass_mailing: "sales", marketing_automation: "sales",
    social: "sales", sms: "sales", survey: "sales", website_sale: "sales",
    account: "finance", account_accountant: "finance", accountant: "finance",
    hr_expense: "finance", payment: "finance",
    hr: "people", hr_attendance: "people", hr_holidays: "people", hr_recruitment: "people",
    hr_payroll: "people", hr_appraisal: "people", hr_timesheet: "people", timesheet_grid: "people",
    mail: "workspace", calendar: "workspace", contacts: "workspace", project: "workspace",
    knowledge: "workspace", documents: "workspace", project_todo: "workspace",
    note: "workspace", sign: "workspace", approvals: "workspace",
    base: "admin", base_setup: "admin",
};
function sectionKeyForXmlid(xmlid) {
    const module = (xmlid || "").split(".")[0];
    if (MODULE_SECTION[module]) {
        return MODULE_SECTION[module];
    }
    if (module.startsWith("hr_")) {
        return "people";
    }
    if (module.startsWith("pos_") || module.startsWith("stock_") || module.startsWith("mrp_")) {
        return "operations";
    }
    if (module.startsWith("account_")) {
        return "finance";
    }
    if (module.startsWith("sale_") || module.startsWith("website_")) {
        return "sales";
    }
    return "other";
}

function normalize(str) {
    // Lowercase + strip Arabic diacritics/tatweel and unify alef/yaa/taa forms
    return (str || "")
        .toLowerCase()
        .replace(/[ً-ٟـ]/g, "")
        .replace(/[أإآ]/g, "ا")
        .replace(/ى/g, "ي")
        .replace(/ة/g, "ه");
}

export class RestaSidebar extends Component {
    static template = "resta_sidebar_theme.Sidebar";
    static props = {};

    setup() {
        this.menuService = useService("menu");
        this.actionService = useService("action");
        this.ui = useState(useService("ui"));
        this.settings = getThemeSettings();
        this.scheme = getCurrentScheme();
        this.searchRef = useRef("search");
        this.rootRef = useRef("root");

        const storedCollapsed = lsGet(LS_COLLAPSED);
        let open = [];
        try {
            open = JSON.parse(lsGet(LS_OPEN) || "[]");
        } catch {
            open = [];
        }
        this.state = useState({
            collapsed:
                storedCollapsed === null
                    ? this.settings.sidebar_default === "collapsed"
                    : storedCollapsed === "1",
            open: new Set(Array.isArray(open) ? open : []),
            query: "",
            mobileOpen: false,
            flyout: null, // { appId, top }
            activeMenuId: null,
            currentAppId: null,
            version: 0,
            fullscreen: false,
        });

        useBus(this.env.bus, "MENUS:APP-CHANGED", () => this.syncActive());
        useBus(this.env.bus, "ACTION_MANAGER:UI-UPDATED", ({ detail: mode }) => {
            if (mode && mode !== "new") {
                this.state.fullscreen = mode === "fullscreen";
                this.applyBodyClasses();
            }
            this.syncActive();
        });
        useBus(this.env.bus, "RS_SIDEBAR:TOGGLE", () => this.toggleMobile());
        useExternalListener(window, "click", this.onWindowClick, { capture: true });
        useExternalListener(window, "keydown", this.onWindowKeydown);

        onMounted(() => {
            this.applyBodyClasses();
            this.syncActive();
        });
        onWillUnmount(() => {
            document.body.classList.remove("rs_has_sidebar", "rs_sidebar_collapsed");
        });
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------

    get companyName() {
        return user.activeCompany?.name || "";
    }

    get logo() {
        const s = this.settings;
        if (this.scheme === "dark") {
            if (s.logo_dark) {
                return { src: s.logo_dark, treatment: "none" };
            }
            if (s.logo_dark_fallback) {
                return { src: s.logo_dark_fallback, treatment: "none" };
            }
            return { src: s.logo_light, treatment: s.logo_dark_treatment || "smart" };
        }
        return { src: s.logo_light, treatment: "none" };
    }

    get logoMark() {
        return this.settings.logo_mark;
    }

    get isDark() {
        return this.scheme === "dark";
    }

    get tooltipPos() {
        // Tooltip toward the content: sidebar sits on the start side.
        return localization.direction === "rtl" ? "left" : "right";
    }

    get schemeLabel() {
        return this.isDark ? _t("Light mode") : _t("Dark mode");
    }

    get expandLabel() {
        return _t("Expand menu");
    }

    get collapseIcon() {
        // Arrow points toward the edge when expanded, toward content when
        // collapsed. Written for LTR: Odoo mirrors fa-angle-* icons in RTL.
        return this.isCollapsed ? "fa fa-angle-double-right" : "fa fa-angle-double-left";
    }

    get collapseLabel() {
        return _t("Collapse menu");
    }

    get isCollapsed() {
        return this.state.collapsed && !this.ui.isSmall;
    }

    getApps() {
        // `version` makes the getter reactive to menu reloads
        void this.state.version;
        return this.menuService.getApps().map((app) => this.menuService.getMenuAsTree(app.id));
    }

    getMenuItemHref(menu) {
        if (!menu.actionID) {
            return "#";
        }
        return `/odoo/${menu.actionPath || "action-" + menu.actionID}`;
    }

    getAppIcon(app) {
        if (this.settings.icon_style !== "odoo") {
            return { type: "resta", svg: iconMarkup(app.rsIcon || iconKeyFor(app.xmlid)) };
        }
        if (app.webIconData) {
            return { type: "img", src: app.webIconData };
        }
        if (app.webIcon && app.webIcon.includes(",")) {
            const [iconClass, color, background] = app.webIcon.split(",");
            if (iconClass && background) {
                return { type: "fa", iconClass, color, background };
            }
        }
        return { type: "letter", letter: (app.name || "?").trim().charAt(0) };
    }

    matches(menu, q) {
        if (normalize(menu.name).includes(q)) {
            return true;
        }
        return (menu.childrenTree || []).some((child) => this.matches(child, q));
    }

    filterTree(items) {
        const q = normalize(this.state.query.trim());
        if (!q) {
            return items;
        }
        return items.filter((item) => this.matches(item, q));
    }

    get groups() {
        const apps = this.filterTree(this.getApps());
        if (!this.settings.group_sections) {
            return [{ key: "all", label: "", apps }];
        }
        const groups = new Map();
        for (const app of apps) {
            let key;
            let label;
            let sequence;
            if (app.rsSection) {
                key = "custom:" + app.rsSection;
                label = app.rsSection;
                sequence = app.rsSectionSequence ?? 50;
            } else {
                key = sectionKeyForXmlid(app.xmlid);
                [sequence, label] = SECTIONS[key];
                label = label();
            }
            if (!groups.has(key)) {
                groups.set(key, { key, label, sequence, apps: [] });
            }
            const group = groups.get(key);
            group.sequence = Math.min(group.sequence, sequence);
            group.apps.push(app);
        }
        return [...groups.values()].sort((a, b) => a.sequence - b.sequence);
    }

    isOpen(menu) {
        if (this.state.query.trim()) {
            return true;
        }
        return this.state.open.has(menu.id);
    }

    isActive(menu) {
        return this.state.activeMenuId === menu.id;
    }

    isAppActive(app) {
        return this.state.currentAppId === app.id;
    }

    containsActive(menu) {
        if (this.isActive(menu)) {
            return true;
        }
        return (menu.childrenTree || []).some((c) => this.containsActive(c));
    }

    // ------------------------------------------------------------------
    // Sync with the action manager
    // ------------------------------------------------------------------

    syncActive() {
        try {
            this._syncActive();
        } catch (error) {
            // Never let menu bookkeeping break the web client
            console.warn("Resta sidebar: could not sync active menu", error);
        }
    }

    _syncActive() {
        this.state.version++;
        const app = this.menuService.getCurrentApp();
        this.state.currentAppId = app ? app.id : null;
        const actionId = this.actionService.currentController?.action?.id;
        let active = null;
        if (app && actionId) {
            const walk = (menu, depth) => {
                if (menu.actionID === actionId && menu.id !== app.id) {
                    if (!active || depth > active.depth) {
                        active = { id: menu.id, depth };
                    }
                }
                for (const child of menu.childrenTree || []) {
                    walk(child, depth + 1);
                }
            };
            walk(this.menuService.getMenuAsTree(app.id), 0);
        }
        this.state.activeMenuId = active ? active.id : null;
        if (app) {
            // Reveal the current app and the path to the active entry
            if (this._lastAppId !== app.id) {
                this.closeOtherApps(app.id);
                this._lastAppId = app.id;
            }
            this.state.open.add(app.id);
            const reveal = (menu) => {
                if (this.containsActive(menu) && menu.childrenTree?.length) {
                    this.state.open.add(menu.id);
                    menu.childrenTree.forEach(reveal);
                }
            };
            this.menuService.getMenuAsTree(app.id).childrenTree.forEach(reveal);
            this.persistOpen();
        }
    }

    persistOpen() {
        lsSet(LS_OPEN, JSON.stringify([...this.state.open]));
    }

    applyBodyClasses() {
        document.body.classList.toggle("rs_has_sidebar", !this.state.fullscreen);
        document.body.classList.toggle("rs_sidebar_collapsed", this.state.collapsed);
    }

    // ------------------------------------------------------------------
    // Handlers
    // ------------------------------------------------------------------

    toggleCollapsed() {
        this.state.collapsed = !this.state.collapsed;
        this.state.flyout = null;
        lsSet(LS_COLLAPSED, this.state.collapsed ? "1" : "0");
        this.applyBodyClasses();
    }

    toggleMobile() {
        this.state.mobileOpen = !this.state.mobileOpen;
    }

    closeMobile() {
        this.state.mobileOpen = false;
    }

    toggleOpen(menu) {
        if (this.state.open.has(menu.id)) {
            this.state.open.delete(menu.id);
        } else {
            if (menu.id === menu.appID) {
                this.closeOtherApps(menu.id);
            }
            this.state.open.add(menu.id);
        }
        this.persistOpen();
    }

    /** Accordion: only one app expanded at a time. */
    closeOtherApps(appId) {
        for (const app of this.menuService.getApps()) {
            if (app.id !== appId) {
                this.state.open.delete(app.id);
            }
        }
    }

    async navigate(menu) {
        this.state.flyout = null;
        this.closeMobile();
        await this.menuService.selectMenu(menu);
    }

    onAppClick(ev, app) {
        if (ev.ctrlKey || ev.metaKey) {
            return; // let the browser open the link in a new tab
        }
        ev.preventDefault();
        if (this.isCollapsed) {
            if (!app.childrenTree.length) {
                this.navigate(app);
                return;
            }
            const rect = ev.currentTarget.getBoundingClientRect();
            const same = this.state.flyout?.appId === app.id;
            this.state.flyout = same ? null : { appId: app.id, top: Math.max(8, rect.top - 8) };
            return;
        }
        if (!app.childrenTree.length) {
            this.navigate(app);
            return;
        }
        this.toggleOpen(app);
    }

    onItemClick(ev, item) {
        if (ev.ctrlKey || ev.metaKey) {
            return;
        }
        ev.preventDefault();
        if (item.childrenTree.length) {
            this.toggleOpen(item);
        } else {
            this.navigate(item);
        }
    }

    get flyoutApp() {
        const id = this.state.flyout?.appId;
        return id ? this.getApps().find((a) => a.id === id) : null;
    }

    onSearchInput(ev) {
        this.state.query = ev.target.value;
    }

    onSearchKeydown(ev) {
        if (ev.key === "Escape") {
            this.state.query = "";
            ev.target.value = "";
        } else if (ev.key === "Enter") {
            // Open the first leaf that matches
            const q = normalize(this.state.query.trim());
            if (!q) {
                return;
            }
            const findLeaf = (items) => {
                for (const item of items) {
                    if (!item.childrenTree.length && item.actionID && normalize(item.name).includes(q)) {
                        return item;
                    }
                    const found = findLeaf(item.childrenTree);
                    if (found) {
                        return found;
                    }
                }
                return null;
            };
            const leaf = findLeaf(this.getApps());
            if (leaf) {
                this.navigate(leaf);
            }
        }
    }

    focusSearch() {
        if (this.isCollapsed) {
            this.toggleCollapsed();
        }
        setTimeout(() => this.searchRef.el?.focus(), 230);
    }

    onWindowClick(ev) {
        if (!this.state.flyout) {
            return;
        }
        const root = this.rootRef.el;
        if (root && !root.contains(ev.target) && !ev.target.closest(".rs_flyout")) {
            this.state.flyout = null;
        }
    }

    onWindowKeydown(ev) {
        if (ev.key === "Escape") {
            this.state.flyout = null;
            this.state.mobileOpen = false;
        }
    }

    onToggleScheme() {
        toggleScheme();
    }

    onLogoClick(ev) {
        ev.preventDefault();
        this.env.bus.trigger("WEBCLIENT:LOAD_DEFAULT_APP");
        this.closeMobile();
    }
}
