/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { testBonatConnection } from "@pos_bonat_loyalty_online/settings/bonat_settings_const";

/*
 * Port of res.config.settings.action_bonat_test_connection() and _bonat_notify().
 *
 * Registered as a VIEW WIDGET, not a field, for a specific reason. The
 * on-premise method reads self.bonat_api_key rather than
 * self.company_id.bonat_api_key so that the button tests the values the merchant
 * just typed, not the last-saved ones; on res.config.settings, related fields
 * only reach the company record when the top-of-form Save runs.
 *
 * A view widget receives the live record, so `this.props.record.data` holds the
 * pending edits and that behaviour is preserved exactly. An ir.actions.client
 * would not have seen them, since a client action launched from a form button
 * gets no unsaved form state.
 *
 * The notification replaces the ir.actions.client / display_notification envelope
 * the Python returned, with the same distinct titles per branch so screen readers
 * announce the outcome.
 */
export class BonatTestConnection extends Component {
    static template = "pos_bonat_loyalty_online.BonatTestConnection";
    static props = {
        record: Object,
        readonly: { type: Boolean, optional: true },
    };

    setup() {
        this.notification = useService("notification");
        this.state = useState({ running: false });
    }

    get data() {
        return this.props.record.data || {};
    }

    /** Hidden until there is a key to test, matching invisible="not bonat_api_key". */
    get visible() {
        return !!this.data.x_bonat_api_key;
    }

    async onClick() {
        if (this.state.running) {
            return;
        }
        this.state.running = true;
        try {
            const result = await testBonatConnection({
                apiKey: this.data.x_bonat_api_key || "",
                merchantId: this.data.x_bonat_merchant_id || "",
                demoMode: !!this.data.x_bonat_demo_mode,
                apiUrl: this.data.x_bonat_api_url || "",
                apiUrlStaging: this.data.x_bonat_api_url_staging || "",
            });
            this.notification.add(result.message, {
                title: result.success ? _t("Bonat — Connected") : _t("Bonat — Connection Failed"),
                type: result.success ? "success" : "danger",
                sticky: false,
            });
        } finally {
            this.state.running = false;
        }
    }
}

registry.category("view_widgets").add("bonat_test_connection", {
    component: BonatTestConnection,
});
