/** @odoo-module **/
// "لوحة البيانات": The Dash inside the back office, under the Resta sidebar.
import { Component, useRef, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

export class TheDashEmbedded extends Component {
    static template = "the_dash.Embedded";
    static props = { ...standardActionServiceProps };

    setup() {
        this.frame = useRef("frame");
        onMounted(() => {
            // Keep the page theme in step with the back office (light / dark).
            const dark = document.documentElement.dataset.colorScheme === "dark" ||
                (document.cookie || "").includes("color_scheme=dark");
            try { window.localStorage.setItem("theDashTheme", dark ? "dark" : "light"); } catch { /* private mode */ }
        });
    }

    get src() {
        const params = this.props.action.params || {};
        const qs = new URLSearchParams();
        if (params.focus) {
            qs.set("focus", params.focus);
        }
        qs.set("embedded", "1");
        return "/thedash?" + qs.toString();
    }
}

registry.category("actions").add("the_dash.embedded", TheDashEmbedded);
