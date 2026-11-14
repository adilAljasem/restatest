/** Subscription ended / account suspended: a full-screen notice in the back office. */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class RestaLockScreen extends Component {
    static template = "resta_saas_tenant.LockScreen";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.state = useState({ data: null, dismissed: false });
        onWillStart(async () => {
            try {
                this.state.data = await this.orm.silent.call("resta.saas.client", "get_lock_state", []);
            } catch {
                this.state.data = null;
            }
        });
    }

    get whatsapp() {
        const d = this.state.data;
        const phone = (d.manager && (d.manager.whatsapp || d.manager.phone)) || (d.support && d.support.whatsapp) || "";
        return phone ? `https://wa.me/${phone.replace(/\D/g, "")}` : "";
    }
}

registry.category("main_components").add("resta_saas_tenant.LockScreen", { Component: RestaLockScreen });
