"""One row per employee per day, read exactly the way payroll reads it.

Payroll takes the FIRST and LAST punch of the day and ignores everything
between, so a midday break is paid as though it were worked, and a day whose
punches do not pair up is skipped entirely -- no late/early, no overtime, no
absence. Neither shows up as a wrong figure anywhere; they show up as a missing
one. This report is where they become visible.

Deliberately mirrors calicut_textiles.public.python.payroll_entry:
  - filter_noise: two punches within 5 minutes are one punch
  - in_time/out_time: times[0] and times[-1], truncated to the minute
so that what HR reads here is what payroll acted on. If that logic changes,
change it here too.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

NOISE_SECONDS = 300


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not (filters.from_date and filters.to_date):
        frappe.throw(_("From Date and To Date are required."))
    if getdate(filters.from_date) > getdate(filters.to_date):
        frappe.throw(_("From Date must not be after To Date."))

    rows = _build_rows(filters)
    return _columns(), rows, None, None, _summary(rows)


# ------------------------------------------------------------------ data


def _filter_noise(times):
    """Two reads seconds apart are one punch, not an in and an out."""
    clean, last = [], None
    for t in times:
        if not last or (t - last).total_seconds() > NOISE_SECONDS:
            clean.append(t)
            last = t
    return clean


def _employees(filters):
    conditions = {"status": "Active"}
    if filters.employee:
        conditions["name"] = filters.employee
    if filters.company:
        conditions["company"] = filters.company

    out = {}
    for e in frappe.get_all(
        "Employee",
        filters=conditions,
        fields=[
            "name",
            "employee_name",
            "company",
            "default_shift",
            "employment_type",
            "custom_exempt_from_biometric_attendance",
        ],
    ):
        # Staff exempt from biometric attendance punch only on the odd day they
        # are in the office. Their pay is driven by hand-entered attendance, so
        # an unpaired punch means nothing and would only be noise here.
        if not filters.include_exempt and e.custom_exempt_from_biometric_attendance:
            continue
        out[e.name] = e
    return out


def _build_rows(filters):
    employees = _employees(filters)
    if not employees:
        return []

    checkins = frappe.get_all(
        "Employee Checkin",
        filters={
            "employee": ["in", list(employees)],
            "time": ["between", [filters.from_date, filters.to_date]],
        },
        fields=["employee", "time"],
        order_by="employee asc, time asc",
    )

    by_day = {}
    for c in checkins:
        by_day.setdefault((c.employee, c.time.date()), []).append(c.time)

    min_break = flt(filters.min_break_minutes)
    rows = []

    for (emp, day), times in by_day.items():
        e = employees[emp]
        clean = _filter_noise(sorted(times))
        count = len(clean)

        in_time = clean[0].replace(second=0, microsecond=0)
        out_time = clean[-1].replace(second=0, microsecond=0)

        span = (out_time - in_time).total_seconds() / 60.0 if count > 1 else 0.0

        if count % 2:
            # Which punch is missing is unknowable, so the punches cannot be
            # paired and any break figure would be a guess. Leave it blank and
            # show the raw times instead -- HR decides what happened.
            issue = "Missing Punch"
            break_minutes = worked_minutes = None
        else:
            # Gaps between the inner pairs: (2nd,3rd), (4th,5th)... Each is time
            # the employee was punched out but is still being paid for.
            break_minutes = sum(
                (clean[i + 1] - clean[i]).total_seconds() / 60.0
                for i in range(1, count - 1, 2)
            )
            worked_minutes = span - break_minutes
            issue = (
                "Break Recorded"
                if break_minutes > 0 and break_minutes >= min_break
                else ""
            )

        rows.append(
            {
                "attendance_date": day,
                "employee": emp,
                "employee_name": e.employee_name,
                "shift": e.default_shift,
                "punches": count,
                "in_time": in_time.time(),
                "out_time": out_time.time() if count > 1 else None,
                "span_minutes": span,
                "break_minutes": break_minutes,
                "worked_minutes": worked_minutes,
                "issue": issue,
                "punch_times": ", ".join(t.strftime("%H:%M") for t in clean),
            }
        )

    if filters.only_issues:
        rows = [r for r in rows if r["issue"]]

    rows.sort(key=lambda r: (r["issue"] == "", r["attendance_date"], r["employee_name"]))
    return rows


def _summary(rows):
    missing = sum(1 for r in rows if r["issue"] == "Missing Punch")
    breaks = [r for r in rows if r["break_minutes"]]
    total_break = sum(r["break_minutes"] for r in breaks)

    return [
        {
            "label": _("Days Reviewed"),
            "value": len(rows),
            "indicator": "Blue",
            "datatype": "Int",
        },
        {
            "label": _("Missing a Punch"),
            "value": missing,
            "indicator": "Red" if missing else "Green",
            "datatype": "Int",
        },
        {
            "label": _("Days With a Break"),
            "value": len(breaks),
            "indicator": "Orange" if breaks else "Green",
            "datatype": "Int",
        },
        {
            "label": _("Break Time Paid as Worked"),
            "value": _("{0} hrs").format(round(total_break / 60.0, 1)),
            "indicator": "Orange" if total_break else "Green",
            "datatype": "Data",
        },
    ]


# ------------------------------------------------------------------ columns


def _columns():
    return [
        {
            "label": _("Issue"),
            "fieldname": "issue",
            "fieldtype": "Data",
            "width": 130,
        },
        {
            "label": _("Date"),
            "fieldname": "attendance_date",
            "fieldtype": "Date",
            "width": 100,
        },
        {
            "label": _("Employee"),
            "fieldname": "employee",
            "fieldtype": "Link",
            "options": "Employee",
            "width": 110,
        },
        {
            "label": _("Name"),
            "fieldname": "employee_name",
            "fieldtype": "Data",
            "width": 180,
        },
        {
            "label": _("Shift"),
            "fieldname": "shift",
            "fieldtype": "Link",
            "options": "Shift Type",
            "width": 130,
        },
        {
            "label": _("Punches"),
            "fieldname": "punches",
            "fieldtype": "Int",
            "width": 80,
        },
        {
            "label": _("In"),
            "fieldname": "in_time",
            "fieldtype": "Time",
            "width": 80,
        },
        {
            "label": _("Out"),
            "fieldname": "out_time",
            "fieldtype": "Time",
            "width": 80,
        },
        {
            "label": _("Span (min)"),
            "fieldname": "span_minutes",
            "fieldtype": "Float",
            "precision": 0,
            "width": 95,
        },
        {
            "label": _("Break (min)"),
            "fieldname": "break_minutes",
            "fieldtype": "Float",
            "precision": 0,
            "width": 100,
        },
        {
            "label": _("Worked (min)"),
            "fieldname": "worked_minutes",
            "fieldtype": "Float",
            "precision": 0,
            "width": 110,
        },
        {
            "label": _("All Punches"),
            "fieldname": "punch_times",
            "fieldtype": "Data",
            "width": 240,
        },
    ]
