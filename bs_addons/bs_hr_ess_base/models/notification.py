from odoo import fields, models


class BsHrNotification(models.Model):
    _name = 'bs.hr.notification'
    _description = 'ESS Portal Notification'
    _order = 'create_date desc, id desc'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    title = fields.Char(required=True)
    body = fields.Text()
    url = fields.Char()
    res_model = fields.Char()
    res_id = fields.Many2oneReference(model_field='res_model')
    is_read = fields.Boolean(index=True)

    def _gc_old(self, days=180):
        limit = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        self.sudo().search([('create_date', '<', limit), ('is_read', '=', True)]).unlink()
