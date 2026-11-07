import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";


patch(PosOrder.prototype, {
    serializeForORM(opts = {}) {
        const data = super.serializeForORM(opts);
        // These values are loaded for display but are written only by the integration.
        // Odoo's POS serializer also sends readonly, non-computed fields.
        for (const name of [
            "blend_financial_required", "blend_financial_state", "blend_financial_error",
            "blend_taxes_prepared", "blend_payment_mapping_id", "blend_payment_method_id",
            "blend_financial_completed_at", "blend_financial_attempts", "blend_refund_required",
        ]) {
            delete data[name];
        }
        return data;
    },
});


patch(PosStore.prototype, {
    getPendingOrder() {
        const pending = super.getPendingOrder(...arguments);
        // Financial Blend orders are finalized by the server acceptance action.
        // A background restaurant sync must not resubmit them while a cashier
        // is working on another order. Explicit syncs refresh them from the server.
        return {
            ...pending,
            orderToUpdate: pending.orderToUpdate.filter(
                (order) => !(
                    order.isSynced &&
                    order.blend_partner_reference &&
                    order.blend_financial_required
                )
            ),
        };
    },

    async setup(...args) {
        await super.setup(...args);
        if (this.config.blend_enabled) {
            this.data.connectWebSocket(
                "BLEND_AVAILABILITY",
                this.onBlendAvailability.bind(this)
            );
            this._blendRefreshVisibility();
            this._blendVisibilityTimer = setInterval(
                () => this._blendRefreshVisibility(),
                30000
            );
        }
    },

    _blendRelationId(value) {
        return value && typeof value === "object" ? value.id : value;
    },

    _blendLocalClock(moment = new Date()) {
        try {
            const parts = new Intl.DateTimeFormat("en-US", {
                timeZone: this.config.blend_timezone || "UTC",
                weekday: "long",
                hour: "2-digit",
                minute: "2-digit",
                hourCycle: "h23",
            }).formatToParts(moment);
            const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
            return {
                day: values.weekday.toLowerCase(),
                minute: Number(values.hour) * 60 + Number(values.minute),
            };
        } catch {
            return {
                day: moment.toLocaleDateString("en-US", { weekday: "long" }).toLowerCase(),
                minute: moment.getHours() * 60 + moment.getMinutes(),
            };
        }
    },

    _blendScheduleVisible(schedule, moment = new Date()) {
        if (!schedule || typeof schedule !== "object") {
            return true;
        }
        const clock = this._blendLocalClock(moment);
        let intervals = schedule[clock.day];
        if (intervals === undefined) {
            return true;
        }
        if (!Array.isArray(intervals)) {
            intervals = [intervals];
        }
        for (const interval of intervals) {
            if (!interval || !Boolean(interval.is_visible)) {
                continue;
            }
            const [startHour, startMinute] = String(interval.start || "00:00")
                .split(":")
                .map(Number);
            const [endHour, endMinute] = String(interval.end || "00:00")
                .split(":")
                .map(Number);
            if (![startHour, startMinute, endHour, endMinute].every(Number.isFinite)) {
                continue;
            }
            const start = startHour * 60 + startMinute;
            const end = endHour * 60 + endMinute;
            if (
                start === end ||
                (start < end && start <= clock.minute && clock.minute < end) ||
                (start > end && (clock.minute >= start || clock.minute < end))
            ) {
                return true;
            }
        }
        return false;
    },

    _blendTemplateVisible(template, moment = new Date()) {
        const templateConfigId = this._blendRelationId(template.blend_config_id);
        if (templateConfigId && templateConfigId !== this.config.id) {
            return false;
        }
        if (
            !template.blend_menu_active ||
            !template.blend_is_available ||
            !this.config.blend_available ||
            !this._blendScheduleVisible(this.config.blend_schedule_json, moment) ||
            !this._blendScheduleVisible(template.blend_schedule_json, moment)
        ) {
            return false;
        }
        const categories = (template.pos_categ_ids || []).filter(
            (category) =>
                this._blendRelationId(category.blend_config_id) === this.config.id &&
                category.blend_external_id
        );
        return (
            !categories.length ||
            categories.some(
                (category) =>
                    category.blend_menu_active &&
                    this._blendScheduleVisible(category.blend_schedule_json, moment)
            )
        );
    },

    _blendRefreshVisibility() {
        const templates = this.models["product.template"]?.getAll?.() || [];
        const moment = new Date();
        for (const template of templates) {
            if (template.blend_external_id) {
                template.available_in_pos = this._blendTemplateVisible(template, moment);
            }
        }
    },

    onBlendAvailability(payload) {
        if (!payload || payload.branch_id !== this.config.blend_branch_id) {
            return;
        }
        if (["item", "modifier"].includes(payload.kind) && payload.product_tmpl_id) {
            const template = this.models["product.template"].get(payload.product_tmpl_id);
            if (template) {
                if (template.blend_external_id) {
                    template.blend_is_available = Boolean(payload.available);
                    template.blend_menu_active = true;
                }
                template.available_in_pos = Boolean(payload.available);
            }
        } else if (payload.kind === "modifier_group") {
            for (const line of this.models["product.modifiers"].filter(
                (candidate) => candidate.blend_group_external_id === payload.external_id
            )) {
                line.blend_group_available = Boolean(payload.available);
            }
        } else if (payload.kind === "branch") {
            this.config.blend_available = Boolean(payload.available);
            this._blendRefreshVisibility();
        }
        if (["item", "modifier"].includes(payload.kind)) {
            this._blendRefreshVisibility();
        }
        if (payload.kind === "menu_sync") {
            this.notification.add(_t("Blend menu was updated. Reload the POS to load new menu records."), {
                type: "info",
            });
        } else if (payload.kind === "branch" && !payload.available) {
            this.notification.add(_t("This branch was paused from Blend."), { type: "warning" });
        } else if (payload.kind === "order") {
            this.notification.add(
                _t("Blend order %(order)s status changed to %(status)s.", {
                    order: payload.order_id,
                    status: payload.status,
                }),
                { type: "info" }
            );
        }
    },

    orderProductBySequenceAndFav(products) {
        const result = super.orderProductBySequenceAndFav(products);
        const itemOrder = this.selectedCategory?.blend_item_order_json;
        if (this.searchProductWord.trim() || !Array.isArray(itemOrder) || !itemOrder.length) {
            return result;
        }
        const positions = new Map(itemOrder.map((externalId, index) => [String(externalId), index]));
        return result.sort((left, right) => {
            const leftPosition = positions.get(String(left.blend_external_id));
            const rightPosition = positions.get(String(right.blend_external_id));
            if (leftPosition === undefined && rightPosition === undefined) {
                return 0;
            }
            if (leftPosition === undefined) {
                return 1;
            }
            if (rightPosition === undefined) {
                return -1;
            }
            return leftPosition - rightPosition;
        });
    },
});


patch(OrderPaymentValidation.prototype, {
    async validateOrder(...args) {
        if (this.order.blend_partner_reference && this.order.blend_financial_required) {
            // Covers both the payment screen and quick validation, before marking
            // the order paid or opening the cash drawer. Native refunds are not enrolled.
            this.pos.dialog.add(AlertDialog, {
                title: _t("Blend order accounting"),
                body: _t("Complete this Blend order using its acceptance action in the order form. If accounting failed, review the Blend Accounting Error before retrying."),
            });
            return false;
        }
        return super.validateOrder(...args);
    },
});


patch(PaymentScreen.prototype, {
    async validateOrder(isForceValidate = false) {
        if (
            this.pos.config.blend_enabled &&
            (
                !this.pos.config.blend_available ||
                !this.pos._blendScheduleVisible(this.pos.config.blend_schedule_json)
            )
        ) {
            this.dialog.add(AlertDialog, {
                title: _t("Blend Branch Paused"),
                body: _t("This branch is currently unavailable in Blend. Resume it before validating new orders."),
            });
            return;
        }
        return super.validateOrder(isForceValidate);
    },
});

