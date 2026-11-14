/** Back office only: tab title and user menu. */
import { patch } from "@web/core/utils/patch";
import { registry } from "@web/core/registry";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { titleService } from "@web/core/browser/title_service";
import { BRAND, BRAND_URL } from "./brand";

// «Orders - Resta» in the browser tab (Odoo falls back to «Odoo» when a page sets no title)
patch(titleService, {
    start() {
        const service = super.start(...arguments);
        const setParts = service.setParts;
        service.setParts = (parts) => {
            setParts({ ...parts, zz_resta: undefined });
            setParts({ zz_resta: BRAND });
        };
        service.setParts({});
        return service;
    },
});

// user menu: no links to odoo.com (documentation, support, Odoo account)
const userMenu = registry.category("user_menuitems");
for (const key of ["documentation", "support", "odoo_account"]) {
    if (userMenu.contains(key)) {
        userMenu.remove(key);
    }
}
userMenu.add("resta_about", () => ({
    type: "item",
    id: "resta_about",
    description: _t("ريستا · resta.sa"),
    href: BRAND_URL,
    callback: () => browser.open(BRAND_URL, "_blank"),
    sequence: 15,
}));
