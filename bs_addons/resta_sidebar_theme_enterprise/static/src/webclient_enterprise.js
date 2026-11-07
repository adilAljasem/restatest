import { patch } from "@web/core/utils/patch";
import { WebClientEnterprise } from "@web_enterprise/webclient/webclient";

// With the sidebar, open the first app instead of the app-grid home menu
// when no action is in the URL. The home menu itself stays available.
patch(WebClientEnterprise.prototype, {
    _loadDefaultApp() {
        const root = this.menuService.getMenu("root");
        const firstAppId = root && root.children[0];
        if (firstAppId) {
            return this.menuService.selectMenu(firstAppId);
        }
        return super._loadDefaultApp(...arguments);
    },
});
