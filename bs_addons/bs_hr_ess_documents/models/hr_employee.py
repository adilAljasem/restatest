from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    ess_document_ids = fields.One2many('bs.hr.document', 'employee_id', string='Documents',
                                       groups='hr.group_hr_user')
    ess_document_count = fields.Integer(compute='_compute_ess_document_count', groups='hr.group_hr_user')
    ess_document_alert_count = fields.Integer(compute='_compute_ess_document_count',
                                              groups='hr.group_hr_user')

    def _compute_ess_document_count(self):
        groups = self.env['bs.hr.document']._read_group(
            [('employee_id', 'in', self.ids)], ['employee_id', 'state'], ['__count'])
        total, alert = {}, {}
        for employee, state, count in groups:
            total[employee.id] = total.get(employee.id, 0) + count
            if state in ('expiring', 'expired', 'missing'):
                alert[employee.id] = alert.get(employee.id, 0) + count
        for employee in self:
            employee.ess_document_count = total.get(employee.id, 0)
            employee.ess_document_alert_count = alert.get(employee.id, 0)

    def action_ess_open_documents(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('bs_hr_ess_documents.action_document')
        action['domain'] = [('employee_id', '=', self.id)]
        action['context'] = {'default_employee_id': self.id}
        return action

    # ------------------------------------------------------------------
    # ESS hooks
    # ------------------------------------------------------------------
    def _ess_attendance_precheck(self, location=None):
        messages = super()._ess_attendance_precheck(location)
        for employee in self:
            expired = self.env['bs.hr.document']._ess_blocking_documents(employee)
            if not expired:
                continue
            if employee.sudo().company_id.ess_block_expired_health:
                names = ', '.join(expired.mapped('type_id.name'))
                messages.append(self.env._(
                    'Check-in refused: your %s has expired. Please renew it and contact HR.', names))
            else:
                expired._ess_notify_supervisor_expired()
        return messages

    def _ess_missing_document_types(self):
        self.ensure_one()
        types = self.env['bs.hr.document.type'].sudo().search([
            ('mandatory_for', '!=', 'none'), ('company_id', 'in', [False, self.company_id.id])])
        have = self.env['bs.hr.document'].sudo().search([('employee_id', '=', self.id)]).type_id
        return types.filtered(lambda t: t not in have and t._is_mandatory_for(self))

    def _ess_dashboard_cards(self):
        cards = super()._ess_dashboard_cards()
        docs = self.env['bs.hr.document'].sudo().search([
            ('employee_id', '=', self.id), ('state', 'in', ('expiring', 'expired'))])
        expired = docs.filtered(lambda d: d.state == 'expired')
        if expired:
            tone, subtitle = 'danger', self.env._('%s expired', len(expired))
        elif docs:
            tone, subtitle = 'warning', self.env._('Renew soon')
        else:
            tone, subtitle = 'success', self.env._('All valid')
        cards.append({
            'key': 'documents', 'title': self.env._('Documents expiring'), 'value': len(docs),
            'subtitle': subtitle, 'url': '/my/hr/documents', 'icon': 'fa-id-card-o', 'tone': tone,
            'sequence': 60,
        })
        return cards

    def _ess_menu_items(self):
        items = super()._ess_menu_items()
        items.append({'key': 'documents', 'title': self.env._('My documents'), 'url': '/my/hr/documents',
                      'icon': 'fa-id-card-o', 'sequence': 70})
        items.append({'key': 'contract', 'title': self.env._('My contract'), 'url': '/my/hr/contract',
                      'icon': 'fa-file-text-o', 'sequence': 75})
        return items
