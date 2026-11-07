/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState } from "@odoo/owl";

/*
 * Port of the API key show/hide behaviour.
 *
 * On-premise this needed four pieces of server-side machinery: a transient
 * `bonat_show_api_key` boolean, an action_bonat_toggle_api_key_visibility()
 * method, and two mutually exclusive <field> tags plus two buttons in the view,
 * one pair for the masked state and one for the revealed state. Every toggle was
 * a server round trip.
 *
 * None of that can exist in an importable module, and none of it needs to. The
 * reveal state is pure UI, so it lives in the browser: one field, one button.
 *
 * Implemented directly against the field contract (props.record / props.name)
 * rather than by subclassing CharField, so it does not depend on that class's
 * internal template structure.
 */
export class BonatApiKeyField extends Component {
    static template = "pos_bonat_loyalty_online.BonatApiKeyField";
    static props = {
        record: Object,
        name: String,
        readonly: { type: Boolean, optional: true },
        placeholder: { type: String, optional: true },
        id: { type: String, optional: true },
    };

    setup() {
        this.state = useState({ revealed: false });
    }

    get value() {
        const raw = this.props.record.data[this.props.name];
        return typeof raw === "string" ? raw : "";
    }

    get inputType() {
        return this.state.revealed ? "text" : "password";
    }

    get toggleTitle() {
        return this.state.revealed ? "Hide API key" : "Show API key";
    }

    get toggleIcon() {
        return this.state.revealed ? "fa-eye-slash" : "fa-eye";
    }

    onChange(ev) {
        this.props.record.update({ [this.props.name]: ev.target.value || false });
    }

    toggleReveal() {
        this.state.revealed = !this.state.revealed;
    }
}

registry.category("fields").add("bonat_api_key", {
    component: BonatApiKeyField,
    displayName: "Bonat API Key",
    supportedTypes: ["char"],
    extractProps: ({ attrs, placeholder }) => ({
        placeholder: placeholder || attrs?.placeholder || "",
    }),
});
