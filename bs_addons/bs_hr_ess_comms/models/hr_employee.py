from datetime import datetime, time, timedelta

from odoo import fields, models
from odoo.fields import Domain


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def _ess_menu_items(self):
        items = super()._ess_menu_items()
        items += [
            {'key': 'news', 'title': self.env._('News'), 'url': '/my/hr/news', 'icon': 'fa-bullhorn',
             'sequence': 60},
            {'key': 'policies', 'title': self.env._('Policies'), 'url': '/my/hr/policies',
             'icon': 'fa-book', 'sequence': 61},
            {'key': 'calendar', 'title': self.env._('Company calendar'), 'url': '/my/hr/calendar',
             'icon': 'fa-calendar', 'sequence': 62},
        ]
        return items

    def _ess_comms_counters(self):
        """(unread, to acknowledge) announcements of the employee."""
        self.ensure_one()
        announcements = self.env['bs.hr.announcement']._ess_visible_for(self)
        acks = self.env['bs.hr.announcement.ack'].sudo().search(
            [('announcement_id', 'in', announcements.ids), ('employee_id', '=', self.id)])
        read = acks.filtered('read_date').announcement_id
        acked = acks.filtered('ack_date').announcement_id
        unread = len(announcements - read)
        to_ack = len(announcements.filtered('require_ack') - acked)
        return unread, to_ack

    def _ess_dashboard_cards(self):
        cards = super()._ess_dashboard_cards()
        unread, to_ack = self._ess_comms_counters()
        cards.append({
            'key': 'news', 'title': self.env._('Announcements'), 'value': unread,
            'subtitle': self.env._('%s to acknowledge', to_ack) if to_ack else self.env._('Unread'),
            'url': '/my/hr/news', 'icon': 'fa-bullhorn',
            'tone': 'danger' if to_ack else ('warning' if unread else 'info'), 'sequence': 70,
        })
        return cards

    # ------------------------------------------------------------------
    # company calendar
    # ------------------------------------------------------------------
    def _ess_public_holidays(self, days=90):
        """Company-wide public holidays (resource.calendar.leaves without resource) in the next days."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        start = datetime.combine(today, time.min)
        end = datetime.combine(today + timedelta(days=days), time.max)
        calendar = self.sudo().resource_calendar_id or self.company_id.resource_calendar_id
        domain = Domain([
            ('resource_id', '=', False),
            ('company_id', 'in', [self.company_id.id, False]),
            ('date_to', '>=', start), ('date_from', '<=', end),
        ])
        domain &= Domain('calendar_id', 'in', [calendar.id, False]) if calendar else Domain('calendar_id', '=', False)
        return self.env['resource.calendar.leaves'].sudo().search(domain, order='date_from')

    def _ess_celebrations(self, ref_date=None):
        """Birthdays and work anniversaries of the colleagues of the same branch in the month.

        Privacy: only the name and the day are returned (never the year/age).
        """
        self.ensure_one()
        if not self.company_id.ess_show_birthdays or not self.work_location_id:
            return []
        ref_date = ref_date or fields.Date.context_today(self)
        colleagues = self.sudo().search([
            ('work_location_id', '=', self.work_location_id.id), ('company_id', '=', self.company_id.id),
            ('active', '=', True)])
        result = []
        for emp in colleagues:
            if emp.birthday and emp.birthday.month == ref_date.month:
                result.append({'day': emp.birthday.day, 'name': emp.name, 'kind': 'birthday'})
            starts = [d for d in emp.with_context(active_test=False).version_ids.mapped('contract_date_start') if d]
            start = min(starts) if starts else None
            if start and start.month == ref_date.month and start.year < ref_date.year:
                result.append({'day': start.day, 'name': emp.name, 'kind': 'anniversary',
                               'years': ref_date.year - start.year})
        result.sort(key=lambda r: (r['day'], r['name']))
        return result
