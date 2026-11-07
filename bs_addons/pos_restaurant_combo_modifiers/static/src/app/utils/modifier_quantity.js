/** @odoo-module **/

export function parseModifierQuantity(rawValue, limits) {
    const normalized = String(rawValue).trim()
        .replace(/[٠-٩]/g, (digit) => String(digit.charCodeAt(0) - 0x0660))
        .replace(/[۰-۹]/g, (digit) => String(digit.charCodeAt(0) - 0x06f0))
        .replace(/[,٫]/g, ".");
    if (!/^\d+(?:\.\d{1,3})?$/.test(normalized)) {
        return { error: "invalid" };
    }
    const value = Number(normalized);
    if (!Number.isFinite(value)) {
        return { error: "invalid" };
    }
    if (
        value > limits.optionMax + 0.0000001 ||
        limits.groupTotal - limits.current + value > limits.groupMax + 0.0000001
    ) {
        return { error: "maximum" };
    }
    if (
        value &&
        limits.rounding &&
        Math.abs(Math.round(value / limits.rounding) * limits.rounding - value) > 0.0000001
    ) {
        return { error: "unit" };
    }
    return { value };
}
