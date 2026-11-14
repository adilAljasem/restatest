/** Payment follow-up: who owes how much, how late, and the next reminder — one tap to send. */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { localization } from "@web/core/l10n/localization";

const NUM = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export class RestaFollowup extends Component {
    static template = "resta_account.Followup";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dir = localization.direction === "rtl" || document.body.classList.contains("o_rtl") ? "rtl" : "ltr";
        this.state = useState({
            data: { rows: [], kpi: {}, levels: [] }, filter: "late", search: "", selected: [], busy: false,
            open: this.props.action?.params?.partner_id || false, postpone: "",
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.data = await this.orm.call("res.partner", "resta_followup_overview", []);
    }

    fmt(v) {
        return NUM.format(v || 0);
    }

    get rows() {
        const q = this.state.search.trim().toLowerCase();
        return this.state.data.rows.filter((r) => {
            if (this.state.filter === "late" && !(r.overdue > 0)) {
                return false;
            }
            if (this.state.filter === "due" && !r.action_due) {
                return false;
            }
            return !q || r.name.toLowerCase().includes(q) || (r.phone || "").includes(q);
        });
    }
    get openRow() {
        return this.state.data.rows.find((r) => r.id === this.state.open);
    }
    lateClass(r) {
        if (!(r.overdue > 0)) {
            return "ok";
        }
        return r.max_days > 60 ? "bad" : r.max_days > 30 ? "warn" : "late";
    }

    setFilter(f) {
        this.state.filter = f;
        this.state.selected = [];
    }
    toggleSelect(id) {
        const s = this.state.selected;
        this.state.selected = s.includes(id) ? s.filter((x) => x !== id) : [...s, id];
    }
    toggleAll(ev) {
        this.state.selected = ev.target.checked ? this.rows.map((r) => r.id) : [];
    }

    async send(ids, levelId = false) {
        if (!ids.length || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const res = await this.orm.call("res.partner", "resta_followup_send", [ids, levelId]);
            let msg = `أُرسلت المتابعة لـ ${res.sent} عميل`;
            if (res.no_email.length) {
                msg += ` — بدون بريد: ${res.no_email.join("، ")} (سُجّلت في سجل العميل)`;
            }
            this.notification.add(msg, { type: res.no_email.length ? "warning" : "success" });
            this.state.selected = [];
            await this.load();
        } finally {
            this.state.busy = false;
        }
    }
    sendSelected() {
        this.send(this.state.selected);
    }
    async postpone(row) {
        if (!this.state.postpone) {
            return;
        }
        await this.orm.call("res.partner", "resta_followup_postpone", [[row.id], this.state.postpone]);
        this.state.postpone = "";
        await this.load();
    }
    async toggleBlocked(row) {
        await this.orm.call("res.partner", "resta_followup_set_blocked", [[row.id], !row.blocked]);
        await this.load();
    }
    async saveNote(row, ev) {
        await this.orm.call("res.partner", "resta_followup_set_note", [[row.id], ev.target.value]);
        row.note = ev.target.value;
    }
    async statement(row) {
        const action = await this.orm.call("res.partner", "action_resta_statement", [[row.id]]);
        this.action.doAction(action);
    }
    openPartner(row) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "res.partner", res_id: row.id, views: [[false, "form"]] });
    }
    openItems(row) {
        this.action.doAction({
            type: "ir.actions.act_window", name: row.name, res_model: "account.move.line",
            views: [[false, "list"], [false, "form"]],
            domain: [["partner_id", "child_of", row.id], ["account_type", "=", "asset_receivable"],
                     ["parent_state", "=", "posted"], ["reconciled", "=", false]],
            context: { create: false },
        });
    }
    openLevels() {
        this.action.doAction("resta_account.action_followup_levels");
    }
}

registry.category("actions").add("resta_account.followup", RestaFollowup);
