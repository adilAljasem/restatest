/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { ask } from "@point_of_sale/app/utils/make_awaitable_dialog";

patch(PosOrder.prototype, {
    setPartner(partner) {
        // Clear consent before standard loyalty code can recalculate rewards.
        if (this.getPartner()?.id !== partner?.id) {
            this.uiState.employeePromotionConsent = null;
        }
        return super.setPartner(...arguments);
    },

    _employeePromotionIsEligible(program) {
        const partner = this.getPartner();
        if (!partner?.is_employee_customer) {
            return false;
        }
        if (!program.employee_eligible_by_partner?.[String(partner.id)]) {
            return false;
        }
        const usage = program.employee_usage_by_partner?.[String(partner.id)] || 0;
        return usage < program.employee_limit && super._programIsApplicable(program);
    },

    _programIsApplicable(program) {
        if (program.for_employees && program.program_type === "promotion") {
            const consent = this.uiState.employeePromotionConsent;
            return (
                this._employeePromotionIsEligible(program) &&
                consent?.partnerId === this.getPartner()?.id &&
                consent.programIds.includes(program.id)
            );
        }
        return super._programIsApplicable(...arguments);
    },
});

patch(PosStore.prototype, {
    async selectPartner(currentOrder = this.getOrder()) {
        const result = await super.selectPartner(...arguments);
        const order = currentOrder;
        if (!order || order.finalized || this.getOrder() !== order) {
            return result;
        }
        const programs = this.models["loyalty.program"].filter(
            (program) => program.for_employees && program.program_type === "promotion"
        );
        const selectedPartner = order.getPartner();
        if (selectedPartner && programs.length) {
            const selectionVersion = (order.uiState.employeePromotionSelectionVersion || 0) + 1;
            order.uiState.employeePromotionSelectionVersion = selectionVersion;
            const isCurrentSelection = () =>
                this.getOrder() === order &&
                !order.finalized &&
                order.getPartner()?.id === selectedPartner.id &&
                order.uiState.employeePromotionSelectionVersion === selectionVersion;
            try {
                const status = await this.data.call(
                    "pos.order",
                    "get_employee_promotion_status",
                    [
                        selectedPartner.id,
                        programs.map((program) => program.id),
                        this.config.id,
                    ]
                );
                if (!isCurrentSelection()) {
                    return result;
                }
                for (const program of programs) {
                    program.update({
                        employee_eligible_by_partner: {
                            ...(program.employee_eligible_by_partner || {}),
                            [String(selectedPartner.id)]: !!status.eligible[String(program.id)],
                        },
                        employee_usage_by_partner: {
                            ...(program.employee_usage_by_partner || {}),
                            [String(selectedPartner.id)]:
                                status.usage[String(program.id)] || 0,
                        },
                    });
                }
                for (const program of programs) {
                    if (!isCurrentSelection()) {
                        return result;
                    }
                    if (
                        !order._employeePromotionIsEligible(program) ||
                        order._programIsApplicable(program)
                    ) {
                        continue;
                    }
                    const confirmed = await ask(this.dialog, {
                        title: _t("Employee Discount"),
                        body: _t(
                            'Apply the employee discount "%s" for %s?',
                            program.name,
                            selectedPartner.name
                        ),
                        confirmLabel: _t("Apply Discount"),
                        cancelLabel: _t("No, Continue Without Discount"),
                    });
                    if (!isCurrentSelection()) {
                        return result;
                    }
                    if (confirmed) {
                        const consent = order.uiState.employeePromotionConsent;
                        order.uiState.employeePromotionConsent = {
                            partnerId: selectedPartner.id,
                            programIds: [
                                ...(consent?.partnerId === selectedPartner.id
                                    ? consent.programIds
                                    : []),
                                program.id,
                            ],
                        };
                    }
                }
                await this.updateRewards();
            } catch {
                // The server-side validation before payment remains authoritative.
            }
        }
        return result;
    },
});

patch(PaymentScreen.prototype, {
    async validateOrder(isForceValidate) {
        const order = this.currentOrder;
        const programs = [
            ...new Map(
                order
                    ._get_reward_lines()
                    .map((line) => line.reward_id?.program_id)
                    .filter(
                        (program) =>
                            program?.for_employees && program.program_type === "promotion"
                    )
                    .map((program) => [program.id, program])
            ).values(),
        ];
        if (programs.length) {
            try {
                const result = await this.pos.data.call(
                    "pos.order",
                    "validate_employee_promotion_programs",
                    [
                        order.getPartner()?.id || false,
                        programs.map((program) => program.id),
                        this.pos.config.id,
                    ]
                );
                if (!result.successful) {
                    this.dialog.add(AlertDialog, {
                        title: _t("Employee Promotion"),
                        body: result.message,
                    });
                    return;
                }
                const partner = order.getPartner();
                for (const program of programs) {
                    program.update({
                        employee_usage_by_partner: {
                            ...(program.employee_usage_by_partner || {}),
                            [String(partner.id)]: result.usage[String(program.id)] || 0,
                        },
                    });
                }
            } catch {
                this.dialog.add(AlertDialog, {
                    title: _t("Employee Promotion"),
                    body: _t("The employee promotion limit could not be verified. Please try again."),
                });
                return;
            }
        }
        const result = await super.validateOrder(...arguments);
        const partner = order.getPartner();
        if (order.finalized && partner && programs.length) {
            for (const program of programs) {
                const usage = program.employee_usage_by_partner || {};
                program.update({
                    employee_usage_by_partner: {
                        ...usage,
                        [String(partner.id)]: (usage[String(partner.id)] || 0) + 1,
                    },
                });
            }
        }
        return result;
    },
});
