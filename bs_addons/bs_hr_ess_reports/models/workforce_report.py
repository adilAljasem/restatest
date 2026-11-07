from odoo import fields, models, tools


class BsHrEssWorkforceReport(models.Model):
    """One line per active employee: nationality group (Saudization), branch, seniority."""
    _name = 'bs.hr.ess.workforce.report'
    _description = 'Workforce analysis'
    _auto = False

    employee_id = fields.Many2one('hr.employee', readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    work_location_id = fields.Many2one('hr.work.location', string='Branch', readonly=True)
    department_id = fields.Many2one('hr.department', readonly=True)
    job_id = fields.Many2one('hr.job', readonly=True)
    country_id = fields.Many2one('res.country', string='Nationality', readonly=True)
    nationality_group = fields.Selection([('saudi', 'Saudi'), ('non_saudi', 'Non-Saudi'), ('unknown', 'Unknown')],
                                         readonly=True)
    is_saudi = fields.Integer(string='Saudis', readonly=True)
    headcount = fields.Integer(readonly=True)
    contract_start = fields.Date(readonly=True)
    seniority_years = fields.Float(readonly=True, aggregator='avg')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                SELECT e.id AS id, e.id AS employee_id, e.company_id, v.work_location_id, v.department_id,
                       v.job_id, v.country_id,
                       CASE WHEN c.code = 'SA' THEN 'saudi' WHEN c.id IS NULL THEN 'unknown'
                            ELSE 'non_saudi' END AS nationality_group,
                       CASE WHEN c.code = 'SA' THEN 1 ELSE 0 END AS is_saudi,
                       1 AS headcount,
                       first_start.d AS contract_start,
                       COALESCE(EXTRACT(EPOCH FROM age(now()::date, first_start.d)) / 31557600.0, 0) AS seniority_years
                  FROM hr_employee e
                  JOIN LATERAL (
                        SELECT * FROM hr_version hv WHERE hv.employee_id = e.id AND hv.active
                      ORDER BY hv.date_version DESC LIMIT 1) v ON TRUE
             LEFT JOIN res_country c ON c.id = v.country_id
             LEFT JOIN LATERAL (
                        SELECT MIN(hv2.contract_date_start) AS d FROM hr_version hv2
                         WHERE hv2.employee_id = e.id) first_start ON TRUE
                 WHERE e.active
            )
        """ % self._table)
