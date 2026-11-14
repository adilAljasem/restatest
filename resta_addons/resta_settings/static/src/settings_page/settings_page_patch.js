/** The native settings page: a short strip on top (back to the settings centre, simple / advanced view). */
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { browser } from "@web/core/browser/browser";
import { user } from "@web/core/user";
import { SettingsPage } from "@web/webclient/settings_form_view/settings/settings_page";

patch(SettingsPage.prototype, {
    setup() {
        super.setup(...arguments);
        this.restaAction = useService("action");
    },
    get restaCanAdvance() {
        return user.isSystem;
    },
    get restaAdvanced() {
        return Boolean(this.env.debug);
    },
    restaOpenHome() {
        this.restaAction.doAction("resta_settings.action_settings_home");
    },
    restaToggleAdvanced() {
        const url = new URL(browser.location.href);
        url.searchParams.set("debug", this.restaAdvanced ? "0" : "1");
        browser.location.href = url.toString();
    },
});
