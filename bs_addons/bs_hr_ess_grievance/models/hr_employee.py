from odoo import models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def _ess_menu_items(self):
        items = super()._ess_menu_items()
        items += [
            {'key': 'grievances', 'title': self.env._('Complaints & suggestions'), 'url': '/my/hr/grievances',
             'icon': 'fa-commenting-o', 'sequence': 70},
            {'key': 'grievance_new', 'title': self.env._('Confidential complaint'),
             'url': '/my/hr/grievance/new', 'icon': 'fa-user-secret', 'sequence': 71, 'new_request': True},
            {'key': 'disciplinary', 'title': self.env._('My disciplinary actions'), 'url': '/my/hr/disciplinary',
             'icon': 'fa-gavel', 'sequence': 72},
        ]
        return items

    def _ess_dashboard_cards(self):
        cards = super()._ess_dashboard_cards()
        pending = self.env['bs.hr.disciplinary'].sudo().search_count([
            ('employee_id', '=', self.id), ('state', '=', 'notified'), ('employee_ack_date', '=', False)])
        if pending:
            cards.append({
                'key': 'disciplinary', 'title': self.env._('Disciplinary actions'), 'value': pending,
                'subtitle': self.env._('To acknowledge'), 'url': '/my/hr/disciplinary',
                'icon': 'fa-gavel', 'tone': 'danger', 'sequence': 75,
            })
        return cards
