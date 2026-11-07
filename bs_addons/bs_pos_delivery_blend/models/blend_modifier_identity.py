"""Stable Blend IDs for one product's choice inside one modifier group."""

from odoo import api, fields, models


class BlendModifierIdentity(models.Model):
    """One row per (product modifier group line, option).

    Blend stores modifiers as menu-wide records keyed by ID, and each record
    belongs to a single group. Reusing an option ID under several products or
    groups makes Blend move it between groups, so a group can end up empty or
    show another group's choices with another product's price. Every option a
    product exposes therefore gets its own ID from this table.
    """

    _name = "bs.blend.modifier.identity"
    _description = "Blend Product Modifier Identity"

    group_line_id = fields.Many2one(
        "pos.product.modifier.group.line", required=True, ondelete="cascade", index=True,
    )
    option_id = fields.Many2one(
        "pos.modifier.option", required=True, ondelete="cascade", index=True,
    )

    _blend_modifier_identity_unique = models.Constraint(
        "UNIQUE(group_line_id, option_id)",
        "A product modifier option can only have one Blend identity.",
    )

    @api.model
    def _ids_for(self, pairs):
        """Return ``{(group_line_id, option_id): identity_id}``, creating missing rows.

        ``ON CONFLICT DO NOTHING`` keeps concurrent menu requests from failing
        on the unique constraint when both create the same identity.
        """
        pairs = sorted({(int(line), int(option)) for line, option in pairs})
        if not pairs:
            return {}
        cr = self.env.cr
        cr.execute(
            """
            INSERT INTO bs_blend_modifier_identity
                   (group_line_id, option_id, create_uid, write_uid, create_date, write_date)
            SELECT pair.line_id, pair.option_id, %s, %s, now() at time zone 'UTC', now() at time zone 'UTC'
              FROM unnest(%s::int[], %s::int[]) AS pair(line_id, option_id)
            ON CONFLICT (group_line_id, option_id) DO NOTHING
            """,
            [self.env.uid, self.env.uid, [p[0] for p in pairs], [p[1] for p in pairs]],
        )
        cr.execute(
            """
            SELECT id, group_line_id, option_id
              FROM bs_blend_modifier_identity
             WHERE (group_line_id, option_id) IN (
                   SELECT * FROM unnest(%s::int[], %s::int[]))
            """,
            [[p[0] for p in pairs], [p[1] for p in pairs]],
        )
        return {(line_id, option_id): row_id for row_id, line_id, option_id in cr.fetchall()}
