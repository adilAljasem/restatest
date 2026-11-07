/** @odoo-module **/

export function arrangeModifierLines(lines, mode) {
    const productLines = lines.filter((line) => !line.is_modifier_line);
    if (mode === "receipt") {
        return productLines;
    }
    return productLines.flatMap((line) => [
        line,
        ...(line.modifier_child_line_ids || []).filter((child) => child.is_modifier_line),
    ]);
}
