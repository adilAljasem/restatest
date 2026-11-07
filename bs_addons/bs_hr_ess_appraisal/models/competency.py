from odoo import api, fields, models
from odoo.exceptions import ValidationError


class BsHrAppraisalCompetency(models.Model):
    _name = 'bs.hr.appraisal.competency'
    _description = 'Appraisal Competency'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    description = fields.Text(translate=True)
    category = fields.Selection([
        ('performance', 'Performance'),
        ('behaviour', 'Behaviour'),
        ('hygiene', 'Hygiene & Food Safety'),
        ('customer', 'Customer'),
        ('attendance', 'Attendance'),
    ], required=True, default='performance')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)


class BsHrAppraisalTemplate(models.Model):
    _name = 'bs.hr.appraisal.template'
    _description = 'Appraisal Template'
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company')
    is_probation = fields.Boolean(string='Probation template',
                                  help='Used by the daily probation reminder.')
    kpi_weight = fields.Float(
        string='Attendance KPI weight (%)', default=0.0,
        help='Share of the final score coming from the attendance rate. '
             'The remaining share comes from the weighted manager scores.')
    line_ids = fields.One2many('bs.hr.appraisal.template.line', 'template_id', string='Competencies', copy=True)
    total_weight = fields.Float(compute='_compute_total_weight')

    @api.depends('line_ids.weight')
    def _compute_total_weight(self):
        for tmpl in self:
            tmpl.total_weight = sum(tmpl.line_ids.mapped('weight'))

    @api.constrains('kpi_weight')
    def _check_kpi_weight(self):
        for tmpl in self:
            if not 0 <= tmpl.kpi_weight <= 100:
                raise ValidationError(self.env._('The KPI weight must be between 0 and 100.'))

    @api.constrains('line_ids')
    def _check_weights(self):
        for tmpl in self:
            if tmpl.line_ids and round(tmpl.total_weight, 2) != 100:
                raise ValidationError(self.env._(
                    'The competency weights of "%s" must sum to 100 (currently %s).',
                    tmpl.name, tmpl.total_weight))


class BsHrAppraisalTemplateLine(models.Model):
    _name = 'bs.hr.appraisal.template.line'
    _description = 'Appraisal Template Line'
    _order = 'sequence, id'

    template_id = fields.Many2one('bs.hr.appraisal.template', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    competency_id = fields.Many2one('bs.hr.appraisal.competency', required=True, ondelete='restrict')
    weight = fields.Float(required=True, default=10.0)

    _competency_uniq = models.Constraint('UNIQUE(template_id, competency_id)',
                                         'A competency can only appear once per template.')
    _weight_positive = models.Constraint('CHECK(weight > 0)', 'The weight must be positive.')
