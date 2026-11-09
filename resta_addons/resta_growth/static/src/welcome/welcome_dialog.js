/** First login of the owner: a short wizard. It can be postponed and is never forced twice. */
import { Component, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";

const STEPS = ["welcome", "journey", "profile", "plan", "consents", "start"];

export class WelcomeDialog extends Component {
    static template = "resta_growth.WelcomeDialog";
    static components = { Dialog };
    static props = ["close"];

    setup() {
        this.orm = useService("orm");
        this.growth = useService("resta_growth");
        this.steps = STEPS;
        this.state = useState({ step: 0, data: null, form: {}, saving: false });
        onWillStart(async () => {
            const data = await this.orm.call("resta.growth.engine", "get_welcome_data", []);
            this.state.data = data;
            const p = data.profile || {};
            this.state.form = {
                restaurant_type_id: p.restaurant_type_id || false,
                city: p.city || "",
                region: p.region || false,
                consent_benchmark: p.consent_benchmark,
                consent_league: p.consent_league,
                consent_financing: p.consent_financing,
                ...(p.off_days || {}),
            };
        });
    }

    get step() { return STEPS[this.state.step]; }
    get isLast() { return this.state.step === STEPS.length - 1; }

    next() { if (!this.isLast) { this.state.step++; } }
    back() { if (this.state.step > 0) { this.state.step--; } }
    go(i) { this.state.step = i; }

    toggleDay(field) { this.state.form[field] = !this.state.form[field]; }
    setType(ev) { this.state.form.restaurant_type_id = parseInt(ev.target.value) || false; }
    setRegion(ev) { this.state.form.region = ev.target.value || false; }

    async save() {
        this.state.saving = true;
        try {
            await this.orm.call("resta.growth.profile", "welcome_save", [{ ...this.state.form, welcome_state: "done" }]);
            await this.growth.load();
        } finally {
            this.state.saving = false;
        }
    }

    async start() {
        await this.save();
        this.props.close();
        this.growth.openJourney();
    }

    async quickTour() {
        await this.save();
        this.props.close();
        this.growth.startTour("resta_growth_welcome");
    }
}
