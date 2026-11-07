/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { onWillUnmount } from "@odoo/owl";
import { Order } from "@pos_enterprise/app/components/order/order";

const { DateTime } = luxon;

// حقول التاريخ المحتملة في pos.prep.state، بالأولوية
const DATE_FIELD_PRIORITY = [
    "last_stage_change", "lastStageChange", "stage_change_date",
    "write_date", "create_date",
];

function toDateTime(v) {
    if (!v) return null;
    if (v instanceof DateTime) return v.isValid ? v : null;
    if (typeof v === "string") {
        const sql = DateTime.fromSQL(v, { zone: "utc" });
        if (sql.isValid) return sql;
        const iso = DateTime.fromISO(v, { zone: "utc" });
        return iso.isValid ? iso : null;
    }
    return null;
}

function dateFieldsOf(record) {
    const names = [...DATE_FIELD_PRIORITY];
    const fields = record?.model?.fields || record?.constructor?.fields || {};
    for (const [name, def] of Object.entries(fields)) {
        if (def?.type === "datetime" && !names.includes(name)) names.push(name);
    }
    return names;
}

// ثواني حالة واحدة، من نفس التاريخ الذي يعتمد عليه عدّاد الدقائق الأصلي
function stateSeconds(state, now) {
    let nativeMinutes = null;
    try {
        nativeMinutes = state.computeDuration();
    } catch {
        nativeMinutes = null;
    }
    for (const name of dateFieldsOf(state)) {
        let dt = null;
        try {
            dt = toDateTime(state[name]);
        } catch {
            dt = null;
        }
        if (!dt) continue;
        const secs = Math.max(0, Math.floor(now.diff(dt, "seconds").seconds));
        // نتأكد أن الحقل يطابق حساب أودو الأصلي (بفارق دقيقة كحد أقصى)
        if (nativeMinutes === null || Math.abs(secs / 60 - nativeMinutes) <= 1.01) {
            return secs;
        }
    }
    return nativeMinutes === null ? null : nativeMinutes * 60;
}

function formatSeconds(s) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const p = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${p(m)}:${p(sec)}` : `${p(m)}:${p(sec)}`;
}

patch(Order.prototype, {
    setup() {
        super.setup(...arguments);
        this.state.kdsSeconds = this._kdsComputeSeconds();
        this.kdsInterval = setInterval(() => {
            this.state.kdsSeconds = this._kdsComputeSeconds();
        }, 1000);
        onWillUnmount(() => clearInterval(this.kdsInterval));
    },

    _kdsComputeSeconds() {
        const now = DateTime.now();
        const states = this.props.order?.states || [];
        let max = null;
        for (const st of states) {
            const s = stateSeconds(st, now);
            if (s !== null && (max === null || s > max)) max = s;
        }
        return max;
    },

    get kdsDurationDisplay() {
        const s = this.state.kdsSeconds;
        return s === null || s === undefined ? this.state.duration : formatSeconds(s);
    },

    get kdsShortNumber() {
        const posOrder = this.order?.pos_order_id;
        let raw = posOrder?.tracking_number;
        if (!raw) {
            // order_name = tracking_number عندما لا يكون للطلب اسم مخصص
            const name = String(this.order?.order_name ?? "");
            raw = /^\d+$/.test(name) ? name : "";
        }
        const digits = String(raw || "").replace(/\D/g, "");
        return digits ? digits.slice(-3).padStart(3, "0") : "";
    },
});
