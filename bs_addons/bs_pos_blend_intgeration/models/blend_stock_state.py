import hashlib

from psycopg2.extras import Json

from odoo import api, fields, models


class BlendStockState(models.Model):
    _name = "blend.stock.state"
    _description = "Last delivered Blend stock availability per menu item"

    config_id = fields.Many2one("pos.config", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="config_id.company_id", store=True, index=True)
    external_reference = fields.Char(required=True)
    availability = fields.Json(required=True)

    _target_unique = models.Constraint(
        "unique(config_id, external_reference)", "One stock delivery state per Blend branch/menu item.",
    )

    @api.model
    def _lock_for_log(self, log):
        """Serialize workers for one target before reading stock or doing HTTP.

        The upsert handles first-delivery races and deliberately writes the row
        even if unchanged. Under Odoo's repeatable-read isolation, a competing
        worker then retries its transaction instead of using an old snapshot.
        Do not catch database concurrency errors and continue with stale state.
        """
        self.flush_model()
        reference = log.external_reference
        if log.event in ("item.updated", "store.updated"):
            destination = hashlib.sha256(log.endpoint_url.encode()).hexdigest()[:16]
            reference = "%s:%s:%s" % (log.event, destination, reference)
        self.env.cr.execute("""
            INSERT INTO blend_stock_state
                (config_id, company_id, external_reference, availability,
                 create_uid, write_uid, create_date, write_date)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (config_id, external_reference)
            DO UPDATE SET write_date = EXCLUDED.write_date
            RETURNING id
        """, [log.config_id.id, log.company_id.id, reference,
              Json(log.stock_check), self.env.uid, self.env.uid])
        state = self.browse(self.env.cr.fetchone()[0])
        state.invalidate_recordset()
        return state
