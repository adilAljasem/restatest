"""End-of-service benefit (مكافأة نهاية الخدمة) per Saudi Labor Law, Art. 84/85.

* Termination by employer / contract expiry (Art. 84):
  half a month wage for each of the first five years, a full month for each
  year after that, pro-rated for fractions of a year.
* Resignation (Art. 85): a third of the above after 2-5 years of service,
  two thirds after 5-10 years, the full amount after 10 years, nothing under 2.

This is an *estimate* shown to employees; payroll remains the reference.
"""


def compute_eos(monthly_wage, service_days, reason='termination'):
    years = max(service_days, 0) / 365.0
    first = min(years, 5.0)
    rest = max(years - 5.0, 0.0)
    full = monthly_wage * (first * 0.5 + rest * 1.0)
    if reason == 'resignation':
        if years < 2:
            ratio = 0.0
        elif years < 5:
            ratio = 1.0 / 3.0
        elif years < 10:
            ratio = 2.0 / 3.0
        else:
            ratio = 1.0
    else:
        ratio = 1.0
    return {
        'years': round(years, 2),
        'full_amount': round(full, 2),
        'ratio': ratio,
        'amount': round(full * ratio, 2),
    }
