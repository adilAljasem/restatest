/** Resta bank reconciliation: pick a bank line, tick what it pays, done. */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { localization } from "@web/core/l10n/localization";

const NUM = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export class RestaBankRec extends Component {
    static template = "resta_account.BankRec";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dir = localization.direction === "rtl" || document.body.classList.contains("o_rtl") ? "rtl" : "ltr";
        this.state = useState({
            journals: [], journalId: false, lines: [], line: null, search: "", candSearch: "", candidates: [],
            selected: [], showDone: false, writeoff: { open: false, account_id: false, label: "", accounts: [], search: "" },
            busy: false,
        });
        onWillStart(async () => {
            this.state.journals = await this.orm.call("account.bank.statement.line", "resta_rec_journals", []);
            const wanted = this.props.action?.params?.journal_id;
            const first = this.state.journals.find((j) => j.id === wanted)
                || this.state.journals.find((j) => j.todo) || this.state.journals[0];
            if (first) {
                await this.chooseJournal(first.id);
            }
        });
    }

    fmt(v) {
        return v < 0 ? `(${NUM.format(-v)})` : NUM.format(v || 0);
    }

    async chooseJournal(id) {
        this.state.journalId = id;
        this.state.line = null;
        await this.loadLines();
        const first = this.state.lines.find((l) => !l.reconciled);
        if (first) {
            await this.selectLine(first);
        }
    }
    async loadLines() {
        this.state.lines = await this.orm.call("account.bank.statement.line", "resta_rec_lines",
            [this.state.journalId, this.state.search, this.state.showDone]);
    }
    async refreshJournals() {
        this.state.journals = await this.orm.call("account.bank.statement.line", "resta_rec_journals", []);
    }
    onSearch(ev) {
        this.state.search = ev.target.value;
        this.loadLines();
    }
    toggleDone() {
        this.state.showDone = !this.state.showDone;
        this.loadLines();
    }

    async selectLine(line) {
        this.state.line = line;
        this.state.selected = [];
        this.state.candSearch = "";
        this.state.writeoff.open = false;
        await this.loadCandidates();
        // preselect an exact match (same amount + partner or invoice number in the label)
        const best = this.state.candidates.find((c) => c.score >= 4);
        if (best && !line.reconciled) {
            this.state.selected = [best.id];
        }
    }
    async loadCandidates() {
        if (!this.state.line || this.state.line.reconciled) {
            this.state.candidates = [];
            return;
        }
        this.state.candidates = await this.orm.call("account.bank.statement.line", "resta_rec_candidates",
            [[this.state.line.id], this.state.candSearch]);
    }
    onCandSearch(ev) {
        this.state.candSearch = ev.target.value;
        this.loadCandidates();
    }
    toggleCandidate(c) {
        const sel = this.state.selected;
        this.state.selected = sel.includes(c.id) ? sel.filter((i) => i !== c.id) : [...sel, c.id];
    }

    get selectedTotal() {
        return this.state.candidates.filter((c) => this.state.selected.includes(c.id)).reduce((s, c) => s + c.residual, 0);
    }
    get leftAfter() {
        if (!this.state.line) {
            return 0;
        }
        return Math.round((this.state.line.amount - this.selectedTotal) * 100) / 100;
    }

    async openWriteoff() {
        this.state.writeoff.open = !this.state.writeoff.open;
        if (this.state.writeoff.open && !this.state.writeoff.accounts.length) {
            this.state.writeoff.accounts = await this.orm.call("account.bank.statement.line", "resta_rec_accounts", [""]);
        }
        this.state.writeoff.label = this.state.writeoff.label || this.state.line.label;
    }
    async searchAccounts(ev) {
        this.state.writeoff.accounts = await this.orm.call("account.bank.statement.line", "resta_rec_accounts", [ev.target.value]);
    }

    async reconcile() {
        if (this.state.busy || !this.state.line) {
            return;
        }
        const wo = this.state.writeoff;
        const writeoff = wo.open && wo.account_id ? { account_id: parseInt(wo.account_id), label: wo.label } : null;
        if (!this.state.selected.length && !writeoff) {
            this.notification.add("اختر فاتورة أو دفعة، أو سجّل المبلغ على حساب.", { type: "warning" });
            return;
        }
        this.state.busy = true;
        try {
            const done = await this.orm.call("account.bank.statement.line", "resta_reconcile",
                [[this.state.line.id], this.state.selected, writeoff]);
            this.notification.add(done.reconciled ? "تمت التسوية ✓" : "تمت مطابقة جزء من المبلغ", { type: "success" });
            const idx = this.state.lines.findIndex((l) => l.id === done.id);
            await this.loadLines();
            await this.refreshJournals();
            const next = this.state.lines.filter((l) => !l.reconciled)[Math.max(0, idx)] || this.state.lines.find((l) => !l.reconciled);
            if (next) {
                await this.selectLine(next);
            } else {
                this.state.line = done;
                this.state.candidates = [];
            }
        } finally {
            this.state.busy = false;
        }
    }
    async unreconcile() {
        const line = await this.orm.call("account.bank.statement.line", "resta_unreconcile", [[this.state.line.id]]);
        await this.loadLines();
        await this.refreshJournals();
        await this.selectLine(line);
    }
    openMove() {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "account.move", res_id: this.state.line.move_id, views: [[false, "form"]] });
    }
    importFile() {
        this.action.doAction("resta_account.action_bank_import", {
            additionalContext: { default_journal_id: this.state.journalId },
        });
    }
    addLines() {
        this.action.doAction("resta_account.action_statement_lines", {
            additionalContext: { default_journal_id: this.state.journalId },
        });
    }
}

registry.category("actions").add("resta_account.bank_rec", RestaBankRec);
