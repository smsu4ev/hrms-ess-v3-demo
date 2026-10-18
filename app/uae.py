"""UAE compliance helpers (Federal Decree-Law 33/2021 and its Executive Regulations).

End-of-Service Gratuity — calculation rules and DOCUMENTED SIMPLIFICATIONS:

  1. Basis: the employee's last-drawn BASIC monthly salary only (Art. 52).
  2. Daily wage = monthly basic / 30 (Art. 54).
  3. Accrual: 21 days' wage per year of service for the first 5 years,
     30 days' wage per year beyond 5 years, pro-rated for partial years (Art. 51).
  4. No gratuity for service of one year or less (Art. 51(3)).
  5. Cap: total gratuity may not exceed 2 years' total wage (Art. 52).
  6. Separation basis (documented interpretation of the widely applied rules):
     - Termination by the employer, or expiry/non-renewal of a fixed-term
       contract: full accrual (100%).
     - Employee resignation: service under 1 year -> 0%; 1 to under 3 years -> 1/3;
       3 to under 5 years -> 2/3; 5 years and above -> 100%.
       (This mirrors the pre-2022 limited-contract scale still widely referenced
       by UAE payroll practice and legal advisories for resignation cases.)
  7. Special cases (death, ill-health, contract breach by employer, probation
     terminations, mutual agreement, unlimited-contract nuances) are NOT modelled —
     they must be checked against the current law before payment.

Reference check encoded in tests: basic AED 3,000, exactly 7 years' service,
employer termination -> (5x21 + 2x30) = 165 days x AED 100/day = AED 16,500.
"""
from datetime import date
import calendar


def daily_basic_wage(monthly_basic):
    """Daily basic wage = monthly basic / 30."""
    return (monthly_basic or 0.0) / 30.0


def service_days(joining_date, last_working_date):
    """Whole days of service between two dates (end date exclusive)."""
    if not joining_date or not last_working_date:
        return 0
    if last_working_date < joining_date:
        return 0
    return (last_working_date - joining_date).days


def gratuity(joining_date, last_working_date, monthly_basic, separation_type="employer"):
    """Calculate end-of-service gratuity.

    Args:
        joining_date: employee's joining date (datetime.date)
        last_working_date: last working date (datetime.date)
        monthly_basic: last-drawn basic monthly salary (AED)
        separation_type: "employer" (termination by employer / contract expiry)
            or "resignation" (employee resigned).

    Returns a dict with the full breakdown. Amounts are rounded to 2 decimals.
    """
    days = service_days(joining_date, last_working_date)
    # Commercial 365-day year basis (keeps exact whole-year cases exact).
    years_exact = days / 365.0
    full_years = int(years_exact)
    remaining_days = days - full_years * 365.0

    daily = daily_basic_wage(monthly_basic)

    # Accrual: 21 days/yr for years 1-5, 30 days/yr beyond 5, pro-rated remainder.
    if full_years <= 5:
        accrual_days = full_years * 21.0 + remaining_days * (21.0 / 365.0)
    else:
        accrual_days = 5 * 21.0 + (full_years - 5) * 30.0 + remaining_days * (30.0 / 365.0)

    base_amount = accrual_days * daily

    # Separation multiplier (documented interpretation).
    if days <= 365:
        multiplier = 0.0
    elif separation_type == "resignation":
        if years_exact >= 5:
            multiplier = 1.0
        elif years_exact >= 3:
            multiplier = 2.0 / 3.0
        elif years_exact >= 1:
            multiplier = 1.0 / 3.0
        else:
            multiplier = 0.0
    else:  # employer termination / contract expiry
        multiplier = 1.0

    gross = base_amount * multiplier
    # Cap at 2 years' total wage.
    cap = (monthly_basic or 0.0) * 24.0
    amount = min(gross, cap)

    return {
        "service_days": days,
        "years_of_service": round(years_exact, 2),
        "full_years": full_years,
        "daily_wage": round(daily, 2),
        "accrual_days": round(accrual_days, 2),
        "base_amount": round(base_amount, 2),
        "separation_type": separation_type,
        "multiplier": round(multiplier, 4),
        "cap": round(cap, 2),
        "capped": gross > cap,
        "amount": round(amount, 2),
    }


def add_months(d, months):
    """Add calendar months to a date (day clamped to month length)."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def probation_end_date(joining_date, probation_months=6):
    """Default UAE probation period is up to 6 months (Art. 9)."""
    if not joining_date:
        return None
    return add_months(joining_date, probation_months)


# On-page reference card for the compliance dashboard.
WORKING_HOURS_NOTES = [
    ("Normal hours", "Maximum 8 hours per day / 48 hours per week (Art. 17). "
     "Commuting time is not counted as working hours."),
    ("Ramadan", "Working hours reduced by 2 hours per day during Ramadan (Art. 17)."),
    ("Overtime", "Up to 2 extra hours per day at +25% of normal hourly pay; "
     "night overtime (9 PM - 4 AM) at +50% (Art. 19)."),
    ("Friday prayer / rest", "Friday is the paid weekly rest day unless the employer "
     "designates otherwise (Art. 21)."),
    ("Wages (WPS)", "Wages must be paid through the Wages Protection System (WPS) "
     "on the due date; the export below is the CSV working input - the formal "
     "MOHRE .sif file must be generated through your bank / WPS agent."),
]
