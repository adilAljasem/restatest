import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

const GROUPS = [
    ["workforce", _t("Workforce")],
    ["attendance", _t("Attendance")],
    ["requests", _t("Requests & leaves")],
    ["finance", _t("Loans, custody & payroll")],
    ["documents", _t("Documents")],
    ["people", _t("Engagement & performance")],
];

export class EssHrDashboard extends Component {
    static template = "bs_hr_ess_reports.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.groups = GROUPS;
        this.state = useState({ data: { tiles: [], branches: [], locations: [] }, location: "", loading: true });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call("bs.hr.ess.dashboard", "get_data", [], {
            location_id: this.state.location || false,
        });
        this.state.loading = false;
    }

    tilesOf(group) {
        return (this.state.data.tiles || []).filter((t) => t.group === group);
    }

    onLocation(ev) {
        this.state.location = ev.target.value;
        this.load();
    }

    open(tile) {
        if (tile.action) {
            this.action.doAction(tile.action);
        }
    }

    openBranch(branch) {
        this.state.location = String(branch.id);
        this.load();
    }
}

registry.category("actions").add("bs_hr_ess_reports.dashboard", EssHrDashboard);
