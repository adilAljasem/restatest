import { onMounted, onPatched } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { SettingsPage } from "@web/webclient/settings_form_view/settings/settings_page";
import { getThemeSettings } from "../js/theme_scheme";
import { iconKeyFor, iconMarkup } from "./icons";

// Settings tabs (left column of the Settings page) get the same Resta icons.
// Done on the rendered DOM rather than by patching the template, so it cannot
// break if another module (e.g. Enterprise) changes that template.
function decorateTabs(root) {
    if (!root || getThemeSettings().icon_style === "odoo") {
        return;
    }
    for (const tab of root.querySelectorAll(".settings_tab .tab[data-key]")) {
        const icon = tab.querySelector(".icon");
        if (!icon || icon.dataset.rsIcon) {
            continue;
        }
        icon.dataset.rsIcon = "1";
        icon.classList.add("rs_glyph", "rs_settings_glyph");
        icon.innerHTML = iconMarkup(iconKeyFor(tab.dataset.key)).toString();
    }
}

patch(SettingsPage.prototype, {
    setup() {
        super.setup(...arguments);
        const run = () => {
            try {
                decorateTabs(document.querySelector(".o_setting_container"));
            } catch (error) {
                console.warn("Resta theme: settings icons skipped", error);
            }
        };
        onMounted(run);
        onPatched(run);
    },
});
