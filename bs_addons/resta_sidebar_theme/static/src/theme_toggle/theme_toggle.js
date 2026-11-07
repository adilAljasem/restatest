import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { getCurrentScheme, toggleScheme } from "../js/theme_scheme";

export class RestaThemeToggle extends Component {
    static template = "resta_sidebar_theme.ThemeToggle";
    static props = {};

    get isDark() {
        return getCurrentScheme() === "dark";
    }

    get label() {
        return this.isDark ? _t("Switch to light mode") : _t("Switch to dark mode");
    }

    onClick() {
        toggleScheme();
    }
}

registry.category("systray").add(
    "resta_sidebar_theme.theme_toggle",
    { Component: RestaThemeToggle },
    { sequence: 1 }
);
