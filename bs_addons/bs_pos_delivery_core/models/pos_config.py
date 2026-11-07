import pytz
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PosConfig(models.Model):
    _inherit = "pos.config"

    delivery_work_time_enabled = fields.Boolean(
        string="Enable Delivery Work Time",
        default=True,
        help="When enabled, delivery orders are accepted only inside the configured "
        "work shifts, even if the POS session is not open.",
    )
    delivery_work_start_time = fields.Float(
        string="Shift 1 Start",
        default=0.0,
        help="Start of the first delivery shift (e.g. 9.0 for 09:00).",
    )
    delivery_work_end_time = fields.Float(
        string="Shift 1 End",
        default=24.0,
        help="End of the first delivery shift (e.g. 23.5 for 23:30). "
        "An end earlier than the start means the shift runs past midnight.",
    )
    delivery_second_shift_enabled = fields.Boolean(
        string="Enable Second Shift",
        default=False,
        help="Add a second daily delivery shift, e.g. 09:00-15:00 and 18:00-02:00.",
    )
    delivery_work_start_time_2 = fields.Float(
        string="Shift 2 Start",
        default=18.0,
        help="Start of the second delivery shift.",
    )
    delivery_work_end_time_2 = fields.Float(
        string="Shift 2 End",
        default=23.0,
        help="End of the second delivery shift. "
        "An end earlier than the start means the shift runs past midnight.",
    )

    @api.constrains(
        "delivery_work_start_time",
        "delivery_work_end_time",
        "delivery_second_shift_enabled",
        "delivery_work_start_time_2",
        "delivery_work_end_time_2",
    )
    def _check_delivery_work_times(self):
        for config in self:
            values = [config.delivery_work_start_time, config.delivery_work_end_time]
            if config.delivery_second_shift_enabled:
                values += [config.delivery_work_start_time_2, config.delivery_work_end_time_2]
            if any(value < 0.0 or value > 24.0 for value in values):
                raise ValidationError(
                    _("Delivery work times for %s must be between 00:00 and 24:00.")
                    % config.display_name
                )
            if config.delivery_second_shift_enabled and (
                config.delivery_work_start_time_2 % 24.0
                == config.delivery_work_end_time_2 % 24.0
            ):
                raise ValidationError(
                    _("The second shift of %s must have different start and end times.")
                    % config.display_name
                )

    def _get_delivery_timezone(self):
        self.ensure_one()
        tz_name = (
            self.company_id.resource_calendar_id.tz
            or self.company_id.partner_id.tz
            or self.env.user.tz
            or "UTC"
        )
        try:
            return pytz.timezone(tz_name)
        except Exception:
            return pytz.UTC

    @staticmethod
    def _is_hour_in_shift(hour, start, end):
        """Return True when ``hour`` is inside the [start, end) shift.

        Equal start/end (e.g. 00:00-24:00) means the whole day. An end earlier
        than the start is an overnight shift (e.g. 18:00-02:00).
        """
        start = float(start or 0.0) % 24.0
        end = min(max(float(end or 0.0), 0.0), 24.0)
        if start == end % 24.0:
            return True
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def get_delivery_work_shifts(self):
        """Return the active (start, end) shifts of this POS."""
        self.ensure_one()
        shifts = [(self.delivery_work_start_time, self.delivery_work_end_time)]
        if self.delivery_second_shift_enabled:
            shifts.append((self.delivery_work_start_time_2, self.delivery_work_end_time_2))
        return shifts

    def is_in_delivery_work_time(self, at=None):
        """Check whether the POS is inside one of its delivery work shifts."""
        self.ensure_one()
        if not self.delivery_work_time_enabled:
            return True

        tz = self._get_delivery_timezone()
        if at is None:
            now_dt = datetime.now(tz)
        elif at.tzinfo is None:
            now_dt = pytz.UTC.localize(at).astimezone(tz)
        else:
            now_dt = at.astimezone(tz)
        hour = now_dt.hour + now_dt.minute / 60.0 + now_dt.second / 3600.0

        return any(
            self._is_hour_in_shift(hour, start, end)
            for start, end in self.get_delivery_work_shifts()
        )
