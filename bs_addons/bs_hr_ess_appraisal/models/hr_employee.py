from datetime import datetime, time

from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def _ess_appraisals_to_review(self):
        """Appraisals waiting for this employee's manager review."""
        self.ensure_one()
        return self.env['bs.hr.ess.appraisal'].sudo().search([
            ('manager_id', '=', self.id), ('employee_id', '!=', self.id), ('state', '=', 'manager_review')])

    def _ess_dashboard_cards(self):
        cards = super()._ess_dashboard_cards()
        Appraisal = self.env['bs.hr.ess.appraisal'].sudo()
        to_fill = Appraisal.search_count([('employee_id', '=', self.id), ('state', '=', 'self_review')])
        to_review = len(self._ess_appraisals_to_review())
        if to_fill or to_review:
            cards.append({
                'key': 'appraisal', 'title': self.env._('Appraisals'), 'value': to_fill + to_review,
                'subtitle': self.env._('Self review to fill') if to_fill else self.env._('Team reviews to do'),
                'url': '/my/hr/appraisals' if to_fill else '/my/hr/team/appraisals',
                'icon': 'fa-star-half-o', 'tone': 'warning', 'sequence': 60,
            })
        today = fields.Date.context_today(self)
        month_start = datetime.combine(today.replace(day=1), time.min)
        kudos = self.env['bs.hr.kudos'].sudo().search_count([
            ('to_employee_id', '=', self.id), ('date', '>=', month_start)])
        cards.append({
            'key': 'kudos', 'title': self.env._('Kudos'), 'value': kudos,
            'subtitle': self.env._('Received this month'), 'url': '/my/hr/kudos',
            'icon': 'fa-thumbs-up', 'tone': 'success' if kudos else 'info', 'sequence': 70,
        })
        return cards

    def _ess_menu_items(self):
        items = super()._ess_menu_items()
        items += [
            {'key': 'appraisals', 'title': self.env._('My appraisals'), 'url': '/my/hr/appraisals',
             'icon': 'fa-star-half-o', 'sequence': 60},
            {'key': 'goals', 'title': self.env._('My goals'), 'url': '/my/hr/goals',
             'icon': 'fa-bullseye', 'sequence': 61},
            {'key': 'kudos', 'title': self.env._('Kudos'), 'url': '/my/hr/kudos',
             'icon': 'fa-thumbs-up', 'sequence': 62},
        ]
        if self._ess_is_manager():
            items.append({'key': 'team_appraisals', 'title': self.env._('Team appraisals'),
                          'url': '/my/hr/team/appraisals', 'icon': 'fa-check-square-o', 'sequence': 31})
        return items
