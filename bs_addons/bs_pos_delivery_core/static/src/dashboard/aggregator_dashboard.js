/** @odoo-module **/

import { registry } from "@web/core/registry";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { dashboardFilters, formatMetric, growthLabel } from "./dashboard_utils";

export class BsAggregatorDashboard extends Component {
    static template = "bs_pos_delivery_core.AggregatorDashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            error: false,
            stale: false,
            data: null,
            options: null,
            lastUpdatedAt: false,
            mode: "overview",
            autoRefresh: true,
            filters: {
                period: "today",
                company_id: "",
                channel: "",
                pos_config_id: "",
                date_from: "",
                date_to: "",
            },
        });
        this.filters = { period: "today" };
        this.snapshotFilters = this.filters;
        this._requestSequence = 0;
        this._destroyed = false;
        this._inFlight = null;
        onWillStart(() => this.load());
        onMounted(() => {
            this.timer = setInterval(() => {
                if (this.state.autoRefresh && !document.hidden && !this._inFlight) {
                    this.load({ silent: true });
                }
            }, 30_000);
        });
        onWillUnmount(() => {
            this._destroyed = true;
            ++this._requestSequence;
            clearInterval(this.timer);
        });
    }

    async load({ silent = false } = {}) {
        const sequence = ++this._requestSequence;
        const filters = { ...this.filters };
        this.state.loading = true;
        const task = (async () => {
            try {
                const [data, options] = await Promise.all([
                    this.orm.call("bs.delivery.dashboard", "get_dashboard_data", [filters]),
                    this.state.options ||
                        this.orm.call("bs.delivery.dashboard", "get_dashboard_options", []),
                ]);
                if (this._destroyed || sequence !== this._requestSequence) {
                    return;
                }
                if (!this.state.options) {
                    this.state.options = options;
                    this.state.filters.company_id = String(options.company_id);
                    this.state.filters.date_from = options.today;
                    this.state.filters.date_to = options.today;
                }
                this.state.data = data;
                // Freeze the rendered dates for drilldowns, even if midnight
                // passes before the next automatic refresh.
                this.snapshotFilters = data.period
                    ? {
                          ...filters,
                          period: "custom",
                          date_from: data.period.date_from,
                          date_to: data.period.date_to,
                      }
                    : filters;
                this.state.error = false;
                this.state.stale = false;
                this.state.lastUpdatedAt = new Date();
                if (!this.modes.some((item) => item.key === this.state.mode)) {
                    this.state.mode = "overview";
                }
            } catch (error) {
                if (this._destroyed || sequence !== this._requestSequence) {
                    return;
                }
                this.state.error = true;
                this.state.stale = Boolean(this.state.data);
                if (!silent || !this.state.data) {
                    this.notification.add(
                        error.data?.message || _t("Unable to load the delivery dashboard."),
                        { type: "danger" },
                    );
                }
            } finally {
                if (!this._destroyed && sequence === this._requestSequence) {
                    this.state.loading = false;
                }
            }
        })();
        this._inFlight = task;
        try {
            await task;
        } finally {
            if (this._inFlight === task) {
                this._inFlight = null;
            }
        }
    }

    applyFilters() {
        const draft = this.state.filters;
        if (
            draft.period === "custom" &&
            (!draft.date_from || !draft.date_to || draft.date_from > draft.date_to)
        ) {
            this.notification.add(_t("Choose a valid start and end date."), {
                type: "warning",
            });
            return;
        }
        if (!this.branches.some((item) => String(item.id) === draft.pos_config_id)) {
            draft.pos_config_id = "";
        }
        if (!this.channels.some((item) => item.key === draft.channel)) {
            draft.channel = "";
        }
        this.filters = dashboardFilters(draft);
        return this.load();
    }

    get branches() {
        return (this.state.options?.branches || []).filter(
            (item) => String(item.company_id) === this.state.filters.company_id,
        );
    }

    get channels() {
        return this.state.options?.channels || [];
    }
    get platformSales() {
        return this.state.data?.platforms || [];
    }
    get branchSales() {
        return this.state.data?.branches || [];
    }
    get businessDayLabel() {
        const businessDay = this.state.data?.business_day;
        return businessDay ? `${_t("Business day starts")} ${businessDay.label}` : "";
    }
    salesTotal(rows, key) {
        return rows.reduce((total, row) => total + Number(row[key] || 0), 0);
    }
    channelInitial(row) {
        return (row.name || row.channel_name || "?").trim().charAt(0).toUpperCase();
    }
    channelStyle(color) {
        return color ? `background:${color};` : "";
    }
    get permissions() {
        return this.state.data?.permissions || {};
    }
    get kpis() {
        return this.state.data?.kpis || {};
    }
    get metrics() {
        return this.state.data?.metrics || {};
    }
    get live() {
        return this.state.data?.live || {};
    }
    get modes() {
        return [
            { key: "overview", label: _t("Overview") },
            { key: "operations", label: _t("Operations") },
            ...(this.permissions.integration
                ? [{ key: "integration", label: _t("Integration") }]
                : []),
        ];
    }
    showSection(...modes) {
        return modes.includes(this.state.mode);
    }
    get overviewHasHistory() {
        return Boolean(
            this.metrics.orders ||
                this.maxTrend ||
                this.kpis.mtd_orders ||
                this.kpis.ytd_gmv ||
                this.kpis.mom_comparable,
        );
    }
    get hasAttention() {
        return Boolean(
            this.live.active_orders ||
                this.kpis.late_orders ||
                (this.permissions.integration &&
                    (this.queueIssueCount || this.live.connection_errors)),
        );
    }
    get recentOrders() {
        const orders = this.state.data?.recent_orders || [];
        return this.state.mode === "overview" ? orders.slice(0, 5) : orders;
    }
    get queueIssueCount() {
        const health = this.state.data?.queue_health || {};
        return (health.webhook_retry || 0) + (health.outbox_retry || 0);
    }
    get growthText() {
        return growthLabel(
            this.kpis,
            _t("vs entire previous month"),
            _t("No previous-month baseline"),
        );
    }
    get periodLabel() {
        const period = this.state.data?.period;
        return period ? `${period.date_from} — ${period.date_to}` : "";
    }
    get scopeLabel() {
        const options = this.state.options;
        const filters = this.snapshotFilters;
        return [
            ...(this.state.data?.company_scope || []).map((item) => item.name),
            options?.channels?.find((item) => item.key === filters.channel)?.name ||
                _t("All platforms"),
            options?.branches.find((item) => item.id === filters.pos_config_id)?.name ||
                _t("All branches"),
        ].join(" · ");
    }
    get maxTrend() {
        return Math.max(0, ...(this.state.data?.trend || []).map((day) => day.amount));
    }
    get maxStateCount() {
        return Math.max(0, ...(this.state.data?.state_breakdown || []).map((item) => item.count));
    }
    trendHeight(amount) {
        return `${this.maxTrend > 0 ? Math.max(0, (amount / this.maxTrend) * 100) : 0}%`;
    }
    stateWidth(count) {
        return `${this.maxStateCount > 0 ? (count / this.maxStateCount) * 100 : 0}%`;
    }
    metric(value, suffix = "") {
        return formatMetric(value, suffix);
    }
    formatAmount(value) {
        const currency = this.state.data?.currency;
        const digits = currency?.decimal_places ?? 2;
        const amount = Number(value || 0).toLocaleString(undefined, {
            minimumFractionDigits: digits,
            maximumFractionDigits: digits,
        });
        const symbol = currency?.symbol || currency?.name || "";
        return currency?.position === "after" ? `${amount} ${symbol}` : `${symbol} ${amount}`;
    }
    stateLabel(value) {
        return (
            {
                received: _t("Received"),
                accepted: _t("Accepted"),
                preparing: _t("Preparing"),
                ready: _t("Ready"),
                picked_up: _t("Picked up"),
                delivered: _t("Delivered"),
                rejected: _t("Rejected"),
                cancelled: _t("Cancelled"),
            }[value] || value
        );
    }
    formatDateTimeValue(value) {
        return value ? formatDateTime(deserializeDateTime(value)) : "—";
    }
    get lastUpdatedLabel() {
        return (
            this.state.lastUpdatedAt?.toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
            }) || ""
        );
    }
    async openMetric(kind, { state = null, providerId = null, channel = null, posConfigId = null } = {}) {
        const kwargs = { state, provider_id: providerId };
        if (channel) {
            kwargs.channel = channel;
        }
        if (posConfigId) {
            kwargs.pos_config_id = posConfigId;
        }
        try {
            const action = await this.orm.call(
                "bs.delivery.dashboard",
                "get_dashboard_action",
                [kind, this.snapshotFilters],
                kwargs,
            );
            if (!this._destroyed) {
                await this.action.doAction(action);
            }
        } catch (error) {
            if (!this._destroyed) {
                this.notification.add(
                    error.data?.message || _t("Unable to open dashboard records."),
                    { type: "danger" },
                );
            }
        }
    }
    openOrder(orderId) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "bs.delivery.order",
            res_id: orderId,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("bs_delivery_aggregator_dashboard", BsAggregatorDashboard);
