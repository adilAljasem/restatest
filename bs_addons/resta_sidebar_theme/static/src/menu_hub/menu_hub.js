import { Component, onMounted, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { MENU_HUB_PATH, MENU_HUB_TAG, collectLeaves, menuHref, menuIcon, normalize } from "./menu_utils";

const SEARCH_FROM = 10; // show the filter box from this many options

// Name of the section page in breadcrumbs restored from the URL (after a page
// reload the web client builds them before the page itself is opened).
let menus = null;
registry.category("services").add("resta_menu_hub", {
    dependencies: ["menu"],
    start(env, { menu }) {
        menus = menu;
        return {};
    },
});
const hubDisplayName = {
    toString() {
        const match = browser.location.pathname.match(/\/(\d+)\/menu-hub(?:\/|$)/);
        const menu = match && menus?.getMenu(parseInt(match[1]));
        return menu?.name || String(_t("Options"));
    },
};

/**
 * Section page: the side menu stays one level deep (app → section). A section
 * that groups several screens opens this page, which lists them as option
 * cards, with a heading for each sub-group.
 */
export class RestaMenuHub extends Component {
    static template = "resta_sidebar_theme.MenuHub";
    static components = { Layout };
    static props = ["*"];
    static path = MENU_HUB_PATH;
    static displayName = hubDisplayName;

    setup() {
        this.menuService = useService("menu");
        this.actionService = useService("action");
        this.state = useState({ query: "" });
        const action = this.props.action || {};
        const id = action.context?.active_id || action.params?.active_id;
        this.menuId = parseInt(id) || null;
        const menu = this.menu;
        if (menu) {
            this.env.config.setDisplayName(menu.name);
            this.menuService.setCurrentMenu(menu);
            // the app goes in the URL too, so a reload opens the page inside the right app
            this.props.updateActionState?.({ menu_id: menu.appID });
            // a link without the app: the web client picks the last app after loading, put ours back
            onMounted(() => {
                browser.setTimeout(() => {
                    if (this.menuService.getCurrentApp()?.id !== menu.appID) {
                        this.menuService.setCurrentMenu(menu);
                    }
                });
            });
        }
    }

    get menu() {
        if (!this.menuId || !this.menuService.getMenu(this.menuId)) {
            return null;
        }
        return this.menuService.getMenuAsTree(this.menuId);
    }

    get sections() {
        const menu = this.menu;
        if (!menu) {
            return [];
        }
        const q = normalize(this.state.query.trim());
        const keep = (leaf) =>
            !q || normalize(leaf.menu.name).includes(q) || leaf.trail.some((t) => normalize(t).includes(q));
        const card = (leaf) => ({ menu: leaf.menu, path: leaf.trail.join(" › "), icon: menuIcon(leaf.menu) });
        const sections = [];
        const direct = (menu.childrenTree || []).filter((c) => !c.childrenTree?.length && c.actionID);
        const directCards = direct.map((m) => ({ menu: m, trail: [] })).filter(keep).map(card);
        if (directCards.length) {
            sections.push({ key: "direct", title: "", cards: directCards });
        }
        for (const child of menu.childrenTree || []) {
            if (!child.childrenTree?.length) {
                continue;
            }
            const groupMatches = q && normalize(child.name).includes(q);
            const cards = collectLeaves(child)
                .filter((leaf) => groupMatches || keep(leaf))
                .map(card);
            if (cards.length) {
                sections.push({ key: "g" + child.id, title: child.name, cards });
            }
        }
        return sections;
    }

    get showSearch() {
        const menu = this.menu;
        return menu ? collectLeaves(menu).length >= SEARCH_FROM : false;
    }

    href(menu) {
        return menuHref(menu);
    }

    onSearchInput(ev) {
        this.state.query = ev.target.value;
    }

    onSearchKeydown(ev) {
        if (ev.key === "Escape") {
            this.state.query = "";
            ev.target.value = "";
        } else if (ev.key === "Enter") {
            const first = this.sections[0]?.cards[0];
            if (first) {
                this.open(null, first.menu);
            }
        }
    }

    async open(ev, menu) {
        if (ev && (ev.ctrlKey || ev.metaKey)) {
            return; // let the browser open the link in a new tab
        }
        ev?.preventDefault();
        if (!menu.actionID) {
            return;
        }
        // Keep this page in the breadcrumbs: «Reports / Sales report»
        await this.actionService.doAction(menu.actionID, {
            onActionReady: () => this.menuService.setCurrentMenu(menu),
        });
    }
}

registry.category("actions").add(MENU_HUB_TAG, RestaMenuHub);
