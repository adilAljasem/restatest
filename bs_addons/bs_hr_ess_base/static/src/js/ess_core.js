import { Interaction } from "@web/public/interaction";
import { registry } from "@web/core/registry";

/**
 * Registers the ESS service worker so the portal can be installed as an app
 * ("Add to home screen") and shows an offline page instead of a browser error.
 */
export class EssCore extends Interaction {
    static selector = ".o_bs_ess";

    start() {
        if ("serviceWorker" in navigator && window.isSecureContext) {
            navigator.serviceWorker.register("/my/hr/sw.js", { scope: "/my/hr" }).catch(() => {});
        }
    }
}

registry.category("public.interactions").add("bs_hr_ess_base.core", EssCore);
