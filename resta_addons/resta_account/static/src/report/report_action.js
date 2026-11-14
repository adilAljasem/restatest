/** Resta financial reports: one screen for every statement (P&L, balance sheet, trial balance,
 *  ledgers, aged balances, VAT). Filters on top, click an amount to see its journal items. */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { download } from "@web/core/network/download";
import { localization } from "@web/core/l10n/localization";

const { DateTime } = luxon;
const NUM = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const AS_OF = ["bs", "aged"];
const ISO = (d) => d.toISODate();

function presetRange(key) {
    const now = DateTime.local();
    switch (key) {
        case "this_month": return [now.startOf("month"), now.endOf("month")];
        case "last_month": { const d = now.minus({ months: 1 }); return [d.startOf("month"), d.endOf("month")]; }
        case "this_quarter": return [now.startOf("quarter"), now.endOf("quarter")];
        case "last_quarter": { const d = now.minus({ quarters: 1 }); return [d.startOf("quarter"), d.endOf("quarter")]; }
        case "this_year": return [now.startOf("year"), now.endOf("year")];
        case "last_year": { const d = now.minus({ years: 1 }); return [d.startOf("year"), d.endOf("year")]; }
        case "today": return [now, now];
        case "end_last_month": { const d = now.minus({ months: 1 }).endOf("month"); return [d, d]; }
        case "end_last_year": { const d = now.minus({ years: 1 }).endOf("year"); return [d, d]; }
    }
    return null;
}

export class RestaReport extends Component {
    static template = "resta_account.Report";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.ui = useService("ui");
        this.kind = this.props.action.params?.kind || "pl";
        this.dir = localization.direction === "rtl" || document.body.classList.contains("o_rtl") ? "rtl" : "ltr";
        const asOf = AS_OF.includes(this.kind);
        const yearly = ["tb", "gl", "partner", "budget"].includes(this.kind);
        const [from, to] = presetRange(asOf ? "today" : yearly ? "this_year" : "this_month");
        this.state = useState({
            preset: asOf ? "today" : yearly ? "this_year" : "this_month",
            options: {
                date_from: ISO(from), date_to: ISO(to), compare: "none", analytic_ids: [], posted_only: true,
                hide_zero: true, partner_type: "receivable", tax_report_id: false,
                budget_id: this.props.action.params?.options?.budget_id || false,
            },
            data: null, loading: false, expanded: {}, filters: { analytic: [], tax_reports: [] },
            branchOpen: false,
        });
        onWillStart(async () => {
            this.state.filters = await this.orm.call("resta.account.report", "get_filters_data", []);
            await this.load();
        });
    }

    get asOf() {
        return AS_OF.includes(this.kind);
    }
    get presets() {
        return this.asOf
            ? [["today", "اليوم"], ["end_last_month", "نهاية الشهر الماضي"], ["end_last_year", "نهاية السنة الماضية"], ["custom", "تاريخ محدد"]]
            : [["this_month", "هذا الشهر"], ["last_month", "الشهر الماضي"], ["this_quarter", "هذا الربع"],
               ["last_quarter", "الربع الماضي"], ["this_year", "هذه السنة"], ["last_year", "السنة الماضية"], ["custom", "فترة محددة"]];
    }
    get canCompare() {
        return ["pl", "bs", "cf"].includes(this.kind);
    }
    get showBranch() {
        return !["cf", "budget"].includes(this.kind);
    }
    get branchLabel() {
        const ids = this.state.options.analytic_ids;
        if (!ids.length) {
            return this.kind === "pl_branch" ? "كل الفروع" : "كل الفروع";
        }
        const names = this.state.filters.analytic.filter((a) => ids.includes(a.id)).map((a) => a.name);
        return names.length > 2 ? `${names.length} فروع` : names.join("، ");
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("resta.account.report", "get_report", [this.kind, this.state.options]);
            this.state.options.tax_report_id = this.state.data.options.tax_report_id || false;
            this.state.options.budget_id = this.state.data.options.budget_id || false;
            this.state.expanded = {};
        } finally {
            this.state.loading = false;
        }
    }

    // ------------------------------------------------------------------ filters
    setPreset(ev) {
        const key = ev.target.value;
        this.state.preset = key;
        const range = presetRange(key);
        if (range) {
            this.state.options.date_from = ISO(range[0]);
            this.state.options.date_to = ISO(range[1]);
            this.load();
        }
    }
    setDate(field, ev) {
        if (ev.target.value) {
            this.state.options[field] = ev.target.value;
            if (this.asOf) {
                this.state.options.date_from = ev.target.value;
            }
            this.load();
        }
    }
    setOption(field, value) {
        this.state.options[field] = value;
        this.load();
    }
    toggleBranch(id) {
        const ids = this.state.options.analytic_ids;
        this.state.options.analytic_ids = ids.includes(id) ? ids.filter((i) => i !== id) : [...ids, id];
        this.load();
    }
    clearBranches() {
        this.state.options.analytic_ids = [];
        this.state.branchOpen = false;
        this.load();
    }

    // ------------------------------------------------------------------ rendering helpers
    fmt(value, col) {
        if (value === null || value === undefined) {
            return "";
        }
        if (col && col.percent) {
            return `${value}%`;
        }
        if (Math.abs(value) < 0.005) {
            return "-";
        }
        return value < 0 ? `(${NUM.format(-value)})` : NUM.format(value);
    }
    rowClass(row) {
        return `o_rr_${row.type} o_rr_level${row.level}`;
    }
    canDrill(row, colIndex) {
        const col = this.state.data.columns[colIndex];
        return row.drill && col && !col.percent && !col.nodrill && row.values[colIndex];
    }

    drill(row, colIndex) {
        if (!this.canDrill(row, colIndex)) {
            return;
        }
        const data = this.state.data;
        const o = data.options;
        const col = data.columns[colIndex];
        const domain = [["company_id", "in", o.company_ids], ["parent_state", "in", o.posted_only ? ["posted"] : ["posted", "draft"]]];
        if (row.account_id) {
            domain.push(["account_id", "=", row.account_id]);
        }
        if (row.domain) {
            domain.push(...row.domain);
        }
        if (row.tag_ids && row.tag_ids.length) {
            domain.push(["tax_tag_ids", "in", row.tag_ids]);
        }
        const mode = col.mode || (col.date_from ? "period" : "upto");
        if (mode === "period") {
            domain.push(["date", ">=", col.date_from], ["date", "<=", col.date_to]);
        } else if (mode === "upto") {
            domain.push(["date", "<=", col.date_to]);
        } else if (mode === "before") {
            domain.push(["date", "<", o.date_from]);
        }
        const analytic = col.analytic_id ? [col.analytic_id] : o.analytic_ids;
        if (analytic && analytic.length) {
            domain.push(["analytic_distribution", "in", analytic]);
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${row.name}`,
            res_model: "account.move.line",
            views: [[false, "list"], [false, "form"]],
            domain,
            context: { create: false },
        });
    }

    async toggle(row) {
        if (!row.expandable) {
            return;
        }
        if (this.state.expanded[row.key]) {
            delete this.state.expanded[row.key];
            return;
        }
        this.state.expanded[row.key] = { loading: true, lines: [] };
        const detail = await this.orm.call("resta.account.report", "get_detail", [
            this.kind, this.state.options, { account_id: row.account_id, partner_id: row.partner_id },
        ]);
        this.state.expanded[row.key] = detail;
    }

    openMove(moveId) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "account.move", res_id: moveId, views: [[false, "form"]] });
    }

    exportXlsx() {
        this.ui.block();
        download({
            url: "/resta_account/report/xlsx",
            data: { kind: this.kind, options: JSON.stringify(this.state.options) },
        }).finally(() => this.ui.unblock());
    }
    print() {
        window.print();
    }
}

registry.category("actions").add("resta_account.report", RestaReport);
