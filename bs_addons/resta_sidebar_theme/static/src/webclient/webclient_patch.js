import { registry } from "@web/core/registry";
import { RestaSidebar } from "../sidebar/sidebar";

// The sidebar is mounted as an independent main component (fixed position),
// so it works with any web client: Community's, or Enterprise's subclass.
// No template of the web client or navbar is patched; a failure here is
// contained by the main components container instead of blanking the page.
registry.category("main_components").add("resta_sidebar_theme.Sidebar", {
    Component: RestaSidebar,
});
