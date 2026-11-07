/** @odoo-module **/

export function dashboardFilters(draft) {
    const filters = { period: draft.period };
    if (draft.company_id) {
        filters.company_ids = [Number(draft.company_id)];
    }
    for (const key of ["provider_id", "pos_config_id"]) {
        if (draft[key]) {
            filters[key] = Number(draft[key]);
        }
    }
    // Ordering platform (HungerStation, Jahez, …), not the integration middleware.
    if (draft.channel) {
        filters.channel = String(draft.channel);
    }
    if (draft.period === "custom") {
        filters.date_from = draft.date_from;
        filters.date_to = draft.date_to;
    }
    return filters;
}

export function formatMetric(value, suffix = "") {
    return value == null || !Number.isFinite(Number(value))
        ? "—"
        : `${Number(value).toFixed(1)}${suffix}`;
}

export function growthLabel(kpis, comparisonLabel, noBaselineLabel) {
    return kpis.mom_comparable
        ? `${formatMetric(kpis.mom_growth_pct, "%")} ${comparisonLabel}`
        : noBaselineLabel;
}
