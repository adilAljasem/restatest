/** Level badge next to the user avatar (owner and branch manager only). */
import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { useDropdownState } from "@web/core/dropdown/dropdown_hooks";
import { WelcomeDialog } from "../welcome/welcome_dialog";

export class GrowthRing extends Component {
    static template = "resta_growth.Ring";
    static props = { pct: Number, size: { type: Number, optional: true }, stroke: { type: Number, optional: true },
                     color: { type: String, optional: true }, slots: { type: Object, optional: true } };
    static defaultProps = { size: 40, stroke: 4 };
    get r() { return (this.props.size - this.props.stroke) / 2; }
    get c() { return 2 * Math.PI * this.r; }
    get offset() { return this.c * (1 - Math.max(0, Math.min(100, this.props.pct)) / 100); }
}

export class GrowthBadge extends Component {
    static template = "resta_growth.Badge";
    static components = { Dropdown, GrowthRing };
    static props = {};

    setup() {
        this.growth = useService("resta_growth");
        this.dialog = useService("dialog");
        this.state = useState(this.growth.state);
        this.dropdown = useDropdownState();
        onWillStart(() => this.growth.load());
        const maybeWelcome = () => {
            const d = this.state.data;
            if (!this.welcomeChecked && d && d.visible && d.is_owner && d.welcome === "new") {
                this.welcomeChecked = true;
                this.dialog.add(WelcomeDialog, {});
            }
        };
        onMounted(() => {
            // the action manager closes dialogs when the first screen renders: wait for it
            this.env.bus.addEventListener("ACTION_MANAGER:UI-UPDATED", maybeWelcome, { once: true });
            this.timer = setTimeout(maybeWelcome, 3500);
        });
        onWillUnmount(() => clearTimeout(this.timer));
    }

    get data() { return this.state.data; }
    get level() { return this.data.level || {}; }
    get trendIcon() {
        const t = this.data.score.trend;
        return t > 0 ? "fa-arrow-up text-success" : t < 0 ? "fa-arrow-down text-danger" : "fa-minus opacity-50";
    }
    get scoreText() {
        return this.data.score.forming ? _t("قيد التكوين") : this.data.score.value;
    }

    whatToDo(cond) {
        this.dropdown.close();
        if (cond.lesson && cond.lesson.tour) {
            this.growth.startTour(cond.lesson.tour);
        } else {
            this.growth.openJourney("learn");
        }
    }

    openJourney() {
        this.dropdown.close();
        this.growth.openJourney();
    }

    async claim() {
        this.dropdown.close();
        await this.growth.claim();
    }
}

registry.category("systray").add("resta_growth.badge", { Component: GrowthBadge }, { sequence: 2 });
