/** Resta settings centre: a setup checklist and one card per area, in the Resta theme style. */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { localization } from "@web/core/l10n/localization";
import { browser } from "@web/core/browser/browser";
import { iconMarkup } from "@resta_sidebar_theme/icons/icons";

export class RestaSettingsHome extends Component {
    static template = "resta_settings.Home";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dir = localization.direction === "rtl" || document.body.classList.contains("o_rtl") ? "rtl" : "ltr";
        this.state = useState({ data: null, search: "", showDone: false });
        onWillStart(async () => {
            this.state.data = await this.orm.call("resta.settings.home", "get_home_data", []);
        });
    }

    icon(key) {
        return iconMarkup(key);
    }

    norm(text) {
        return (text || "").toLowerCase().replace(/[إأآ]/g, "ا").replace(/ة/g, "ه").trim();
    }
    get query() {
        return this.norm(this.state.search);
    }
    get sections() {
        const q = this.query;
        if (!q) {
            return this.state.data.sections;
        }
        return this.state.data.sections
            .map((s) => ({
                ...s,
                cards: s.cards.filter((c) =>
                    [c.title, c.desc, ...c.links.map((l) => l.label)].some((t) => this.norm(t).includes(q))
                ),
            }))
            .filter((s) => s.cards.length);
    }
    get checklist() {
        const items = this.state.data.checklist;
        const q = this.query;
        const list = q ? items.filter((i) => this.norm(i.title + " " + i.hint).includes(q)) : items;
        return this.state.showDone || q ? list : list.filter((i) => !i.done).concat(list.filter((i) => i.done).slice(0, 2));
    }

    run(action) {
        if (action) {
            this.action.doAction(action);
        }
    }
    openTab(card) {
        if (card.locked) {
            return this.openJourney();
        }
        if (card.action) {
            return this.run(card.action);
        }
        this.action.doAction("base_setup.action_general_configuration", {
            additionalContext: { module: card.tab },
        });
    }
    openLink(card, link) {
        if (card.locked) {
            return this.openJourney();
        }
        this.run(link.action);
    }
    openJourney() {
        const growth = this.state.data.growth;
        this.run((growth && growth.action) || "resta_growth.action_growth_journey");
    }
    openAllSettings() {
        this.action.doAction("base_setup.action_general_configuration");
    }
    openAdvanced() {
        browser.location.href = "/odoo/settings?debug=1";
    }
}

registry.category("actions").add("resta_settings.home", RestaSettingsHome);
