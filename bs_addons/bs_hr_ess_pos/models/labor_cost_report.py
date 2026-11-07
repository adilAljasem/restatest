from odoo import fields, models, tools


class BsHrEssLaborCostReport(models.Model):
    """Daily labor cost vs POS net sales per branch.

    Labor cost = worked hours x hourly cost (employee hourly cost, or wage / 240 when empty).
    Days use the timezone of the company working calendar.
    """
    _name = 'bs.hr.ess.labor.cost.report'
    _description = 'Labor cost vs sales'
    _auto = False
    _order = 'date desc'

    date = fields.Date(readonly=True)
    location_id = fields.Many2one('hr.work.location', string='Branch', readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    net_sales = fields.Monetary(readonly=True)
    worked_hours = fields.Float(readonly=True)
    labor_cost = fields.Monetary(readonly=True)
    labor_ratio = fields.Float(string='Labor / sales (%)', readonly=True, aggregator=None)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                WITH sales AS (
                    SELECT pc.ess_work_location_id AS location_id,
                           (po.date_order AT TIME ZONE 'UTC'
                                AT TIME ZONE COALESCE(rc.tz, 'UTC'))::date AS date,
                           SUM(po.amount_total - po.amount_tax) AS net_sales
                      FROM pos_order po
                      JOIN pos_session ps ON ps.id = po.session_id
                      JOIN pos_config pc ON pc.id = ps.config_id
                      JOIN res_company c ON c.id = po.company_id
                 LEFT JOIN resource_calendar rc ON rc.id = c.resource_calendar_id
                     WHERE pc.ess_work_location_id IS NOT NULL
                       AND po.state IN ('paid', 'done')
                  GROUP BY 1, 2
                ), labor AS (
                    SELECT a.ess_location_id AS location_id, a.date AS date,
                           SUM(a.worked_hours) AS worked_hours,
                           SUM(a.worked_hours * COALESCE(NULLIF(e.hourly_cost, 0), (
                               SELECT v.wage / 240.0 FROM hr_version v
                                WHERE v.employee_id = e.id AND v.active
                             ORDER BY v.date_version DESC LIMIT 1), 0)) AS labor_cost
                      FROM hr_attendance a
                      JOIN hr_employee e ON e.id = a.employee_id
                     WHERE a.ess_location_id IS NOT NULL AND a.check_out IS NOT NULL
                  GROUP BY 1, 2
                )
                SELECT row_number() OVER () AS id,
                       COALESCE(s.location_id, l.location_id) AS location_id,
                       COALESCE(s.date, l.date) AS date,
                       loc.company_id AS company_id,
                       COALESCE(s.net_sales, 0) AS net_sales,
                       COALESCE(l.worked_hours, 0) AS worked_hours,
                       COALESCE(l.labor_cost, 0) AS labor_cost,
                       CASE WHEN COALESCE(s.net_sales, 0) > 0
                            THEN 100.0 * COALESCE(l.labor_cost, 0) / s.net_sales ELSE 0 END AS labor_ratio
                  FROM sales s
             FULL JOIN labor l ON l.location_id = s.location_id AND l.date = s.date
                  JOIN hr_work_location loc ON loc.id = COALESCE(s.location_id, l.location_id)
            )
        """ % self._table)
