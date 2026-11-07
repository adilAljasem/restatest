/** Resta platform panel: subscription status, account manager, updates & messages, feedback. */
import { Component, markup, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { useDropdownState } from "@web/core/dropdown/dropdown_hooks";
import { Dialog } from "@web/core/dialog/dialog";

const KIND_LABELS = {
    feature: "ميزة جديدة", enhancement: "تحسين", fix: "إصلاح", maintenance: "صيانة مجدولة", message: "رسالة",
};

function relativeDate(str) {
    if (!str) { return ""; }
    const d = new Date(str.replace(" ", "T") + "Z");
    const days = Math.floor((Date.now() - d.getTime()) / 86400000);
    if (days <= 0) { return "اليوم"; }
    if (days === 1) { return "أمس"; }
    if (days < 7) { return `قبل ${days} أيام`; }
    if (days < 14) { return "قبل أسبوع"; }
    if (days < 60) { return `قبل ${Math.floor(days / 7)} أسابيع`; }
    return d.toLocaleDateString("ar-SA");
}

class RestaNoticeDialog extends Component {
    static template = "resta_saas_tenant.NoticeDialog";
    static components = { Dialog };
    static props = ["close", "title", "body", "blocking?", "manager?", "support?"];
    get body() { return markup(this.props.body || ""); }
}

export class RestaSystray extends Component {
    static template = "resta_saas_tenant.Systray";
    static components = { Dropdown };
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.dropdown = useDropdownState({ onOpen: () => this.onOpen() });
        this.state = useState({ data: null, suggest: false, suggestText: "", notes: {}, sending: false });
        this.kindLabels = KIND_LABELS;
        onWillStart(() => this.load());
        // The action manager closes every dialog when it renders the first screen:
        // show notices once that screen is up (event), with a timer as fallback.
        const show = () => {
            if (!this.noticesShown) {
                this.noticesShown = true;
                this.showBlockingNotices();
            }
        };
        onMounted(() => {
            this.env.bus.addEventListener("ACTION_MANAGER:UI-UPDATED", show, { once: true });
            this.noticeTimer = setTimeout(show, 4000);
        });
        onWillUnmount(() => clearTimeout(this.noticeTimer));
    }

    async load() {
        try {
            this.state.data = await this.orm.call("resta.saas.client", "get_panel_data", []);
        } catch {
            this.state.data = { linked: false };
        }
    }

    get data() { return this.state.data || {}; }
    get unread() { return (this.data.updates || []).filter((u) => u.unread).length; }
    get showPill() { return this.data.linked && ["trial", "expired", "suspended"].includes(this.data.state); }
    get pillClass() {
        if (this.data.state === "trial") { return this.data.days_left <= 3 ? "o_resta_pill_warn" : "o_resta_pill_trial"; }
        return "o_resta_pill_danger";
    }
    get pillText() {
        if (this.data.state === "trial") { return `تجربة · ${Math.max(this.data.days_left, 0)} يوم`; }
        return this.data.state_label;
    }
    get whatsapp() {
        const n = (this.data.manager || {}).whatsapp || (this.data.support || {}).whatsapp;
        return n ? `https://wa.me/${n}` : false;
    }
    body(u) { return markup(u.body || ""); }
    when(u) { return relativeDate(u.date); }

    showBlockingNotices() {
        const d = this.data;
        if (!d.linked) { return; }
        if (["expired", "suspended"].includes(d.state)) {
            this.dialog.add(RestaNoticeDialog, {
                title: d.state === "expired" ? "انتهى اشتراك ريستا" : "تم إيقاف الحساب",
                body: d.state === "expired"
                    ? "<p>انتهت فترة اشتراكك. نقاط البيع متوقفة حتى تجديد الاشتراك، وبياناتك محفوظة.</p>"
                    : "<p>تم إيقاف حساب مطعمك مؤقتاً. تواصل مع مدير حسابك لإعادة التفعيل.</p>",
                blocking: d.state === "suspended" && !d.is_admin,
                manager: d.manager, support: d.support,
            });
            return;
        }
        const important = (d.updates || []).find((u) => u.important && u.unread);
        if (important) {
            this.dialog.add(RestaNoticeDialog, { title: important.title, body: important.body },
                { onClose: () => this.orm.call("resta.saas.client", "mark_read", [[important.id]]) });
        }
    }

    async onOpen() {
        await this.load();
        if (this.unread) {
            const ids = this.data.updates.filter((u) => u.unread).map((u) => u.id);
            await this.orm.call("resta.saas.client", "mark_read", [ids]);
        }
    }

    async send(kind, { message = "", rating = null, update = null } = {}) {
        this.state.sending = true;
        try {
            await this.orm.call("resta.saas.client", "send_feedback", [kind], {
                message, rating, update_id: update ? update.id : null,
            });
            this.notification.add("شكراً لك، وصلت ملاحظتك لفريق ريستا", { type: "success" });
            return true;
        } catch {
            this.notification.add("تعذّر الإرسال، حاول لاحقاً", { type: "danger" });
            return false;
        } finally {
            this.state.sending = false;
        }
    }

    async sendSuggestion() {
        if (!this.state.suggestText.trim()) { return; }
        if (await this.send("suggestion", { message: this.state.suggestText })) {
            this.state.suggestText = "";
            this.state.suggest = false;
        }
    }

    async rate(update, rating) {
        if (await this.send("rating", { rating: String(rating), update, message: this.state.notes[update.id] || "" })) {
            update.rated = true;
        }
    }

    async sendNote(update) {
        const text = (this.state.notes[update.id] || "").trim();
        if (!text) { return; }
        if (await this.send("rating", { message: text, update })) {
            this.state.notes[update.id] = "";
        }
    }
}

registry.category("systray").add("resta_saas_tenant.systray", { Component: RestaSystray }, { sequence: 1 });
