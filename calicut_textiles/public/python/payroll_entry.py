import frappe
from frappe.utils import getdate, add_days, get_time, flt
from datetime import datetime, timedelta, time
from collections import defaultdict

# =====================================================
# VALIDATIONS
# =====================================================

def get_max_consecutive_leave(leave_type):
    value = frappe.db.get_value(
        "Leave Type",
        leave_type,
        "max_continuous_days_allowed"
    )
    if not value:
        return 0
    return int(value)

def get_leave_encashment_component(leave_type):
    component = frappe.db.get_value(
        "Leave Type",
        leave_type,
        "earning_component"
    )
    if not component:
        frappe.throw(
            f"Earning Component not set for {leave_type}.",
            title="Leave Type Configuration Error"
        )
    return component

# =====================================================
# ENTRY POINT
# =====================================================
@frappe.whitelist()
def enqueue_payroll_processing(payroll_entry):
    process_payroll_entry(payroll_entry)

# =====================================================
# MAIN PAYROLL FLOW
# =====================================================
def process_payroll_entry(payroll_entry):
    pe = frappe.get_doc("Payroll Entry", payroll_entry)
    company_account = frappe.db.get_value(
        "Company", {"name": pe.company}, "default_employee_advance_account"
    )

    employees = [
        row.employee for row in pe.employees
        if not row.is_salary_withheld
    ]

    if not employees:
        frappe.throw("No eligible employees found.")

    start_date = getdate(pe.start_date)
    end_date = getdate(pe.end_date)

    employee_map = load_employees(employees)
    holiday_map = load_holidays(employee_map)
    checkin_map = load_checkins(employees, start_date, end_date)

    create_overtime(
        pe, employees, employee_map, checkin_map, holiday_map
    )
    for emp in employees:
        create_employee_advances_deductions(start_date, end_date, emp, company_account)
        process_attendance(
            emp,
            start_date,
            end_date,
            employee_map,
            holiday_map,
            checkin_map
        )

def create_employee_advances_deductions(start, end, employee, company_account):
    salary_component = frappe.db.get_value(
        "Salary Component Account",
        {
            "account": company_account,
            "parent": "Employee Advance"
        },
        "parent"
    )

    if not salary_component:
        return

    employee_advances = frappe.get_all(
        "Employee Advance",
        filters={
            "employee": employee,
            "posting_date": ["between", [start, end]],
            "docstatus": 1,
        },
        fields=["name", "claimed_amount", "paid_amount"]
    )

    for adv in employee_advances:
        advance_amount = (adv.paid_amount or 0) - (adv.claimed_amount or 0)

        if advance_amount <= 0:
            continue

        existing = frappe.get_all(
            "Additional Salary",
            filters={
                "employee": employee,
                "salary_component": salary_component,
                "payroll_date": end,
                "ref_docname": adv.name,
                "docstatus": ["!=", 2],
            },
            fields=["name", "amount", "docstatus"],
            limit=1
        )

        if existing:
            existing_doc = frappe.get_doc("Additional Salary", existing[0].name)

            if float(existing_doc.amount) == float(advance_amount):
                continue

            existing_doc.flags.ignore_permissions = True

            if existing_doc.docstatus == 1:
                existing_doc.cancel()

            existing_doc.delete(ignore_permissions=True)

        doc = frappe.new_doc("Additional Salary")
        doc.employee = employee
        doc.salary_component = salary_component
        doc.amount = advance_amount
        doc.payroll_date = end
        doc.custom_is_system_generated = 1
        doc.ref_doctype = "Employee Advance"  # 🔑 Reference
        doc.ref_docname = adv.name  # 🔑 Reference
        doc.flags.ignore_permissions = True

        doc.insert(ignore_permissions=True)
        doc.submit()



        
# =====================================================
# DATA LOADERS
# =====================================================

def load_employees(employees):
    data = frappe.get_all(
        "Employee",
        filters={"name": ["in", employees]},
        fields=["name", "holiday_list", "default_shift", "date_of_joining", "employment_type",
                "custom_exempt_from_biometric_attendance"]
    )
    return {d.name: d for d in data}

def load_holidays(employee_map):
    holiday_lists = {
        e.holiday_list for e in employee_map.values()
        if e.holiday_list
    }

    holidays = frappe.get_all(
        "Holiday",
        filters={"parent": ["in", list(holiday_lists)]},
        fields=["parent", "holiday_date"]
    )

    holiday_map = defaultdict(set)
    for h in holidays:
        holiday_map[h.parent].add(h.holiday_date)

    return holiday_map

def load_checkins(employees, start, end):
    rows = frappe.get_all(
        "Employee Checkin",
        filters={
            # Frappe already stretches the end of a date range to 23:59:59, so
            # `end + 1 day` pulled in the whole of the following day: June payroll
            # was counting 1 July's punches as overtime and late/early.
            "employee": ["in", employees],
            "time": ["between", [start, end]]
        },
        fields=["employee", "time", "custom_late_early","custom_late_coming_minutes","custom_early_going_minutes"],
        order_by="time asc"
    )

    result = defaultdict(lambda: defaultdict(list))
    for r in rows:
        result[r.employee][r.time.date()].append(r)

    return result

# =====================================================
# OVERTIME
# =====================================================

def create_overtime(pe, employees, employee_map, checkin_map, holiday_map):
    settings = frappe.get_single("Calicut Textiles Settings")

    threshold = settings.threshold_overtime_minutes or 0
    excluded_shift = settings.shift
    ot_component = settings.ot_component

    # Grace period on both sides of the shift. Beyond it, late/early counts in
    # full minutes from the true shift boundary (not from the end of the grace).
    grace = settings.threshold_early_minutes or 0
    early_component = settings.early_component

    for emp in employees:

        # Skip if OT already created
        if frappe.db.exists(
            "Additional Salary",
            {
                "employee": emp,
                "salary_component": ot_component,
                "payroll_date": pe.end_date,
                "custom_is_overtime": 1,
                "docstatus": 1
            }
        ):
            continue

        emp_doc = employee_map.get(emp)
        if not emp_doc:
            continue

        # Exempt staff punch only on the odd day they happen to be in the office.
        # Reading late/early or overtime out of that would penalise them for the
        # very pattern that makes them exempt.
        if emp_doc.get("custom_exempt_from_biometric_attendance"):
            continue

        shift_name = emp_doc.default_shift
        if not shift_name or shift_name == excluded_shift:
            continue

        is_part_time = emp_doc.employment_type == "Part-time"

        shift = frappe.get_doc("Shift Type", shift_name)
        shift_hours = get_shift_hours(shift)
        if shift_hours <= 0 and not is_part_time:
            continue

        holidays = holiday_map.get(emp_doc.holiday_list, set())

        total_ot_minutes = 0
        total_early_late_minutes = 0

        for date, rows in checkin_map.get(emp, {}).items():
            times = filter_noise([r.time for r in rows])
            if len(times) < 2:
                continue

            # The biometric export the payroll workbook is built from records
            # HH:MM only, while CrossChex returns seconds. Work to the minute so
            # spans and late/early agree with the workbook instead of losing up
            # to a minute a day to truncation.
            in_time = times[0].replace(second=0, microsecond=0)
            out_time = times[-1].replace(second=0, microsecond=0)

            # Part-timers have no shift to be late for -- every worked minute is
            # paid at their hourly rate, so the whole span is "overtime".
            if is_part_time:
                worked_minutes = minutes(out_time - in_time)
                if worked_minutes > 0:
                    total_ot_minutes += worked_minutes
                continue

            # ---------------- HOLIDAY LOGIC ----------------
            if date in holidays:
                worked_minutes = minutes(out_time - in_time)
                if worked_minutes > 0:
                    total_ot_minutes += worked_minutes
                continue  # CRITICAL: skip early/late completely
            # ------------------------------------------------

            shift_start, shift_end = shift_bounds(shift, date)

            normal_start = shift_start - timedelta(minutes=threshold)
            normal_end = shift_end + timedelta(minutes=threshold)

            normal_lateearly_start = shift_start + timedelta(minutes=grace)
            normal_lateearly_end = shift_end - timedelta(minutes=grace)

            # ---------------- OVERTIME ----------------
            if in_time < normal_start:
                total_ot_minutes += threshold + minutes(normal_start - in_time)

            if out_time > normal_end:
                total_ot_minutes += threshold + minutes(out_time - normal_end)
            # --------------------------------------------

            # ----------- EARLY / LATE (NON-HOLIDAY ONLY) -----------
            if in_time > normal_lateearly_start:
                total_early_late_minutes += grace + minutes(in_time - normal_lateearly_start)

            # Staying until the late-evening cutoff waives early-going for the
            # day, however the shift is defined.
            waiver = late_exit_waiver_time(date)
            if out_time < normal_lateearly_end and not (waiver and out_time >= waiver):
                total_early_late_minutes += grace + minutes(normal_lateearly_end - out_time)
            # ------------------------------------------------------

        if is_part_time:
            rate = get_hourly_rate(emp) / 60.0
        else:
            rate = get_per_minute_salary(emp, pe.start_date, pe.end_date, shift_hours)

        if total_ot_minutes > 0:
            create_monthly_overtime(
                emp,
                pe.end_date,
                total_ot_minutes,
                round(rate * total_ot_minutes, 2),
                ot_component,
                is_overtime=True,
            )

        if total_early_late_minutes > 0:
            create_monthly_overtime(
                emp,
                pe.end_date,
                total_early_late_minutes,
                round(rate * total_early_late_minutes, 2),
                early_component,
                is_overtime=False,
            )

# =====================================================
# ATTENDANCE / LEAVE
# =====================================================
def clear_system_generated_attendance(emp, start, end):
    """Drop the Absent/leave rows a previous run created for this period.

    Punches are re-synced from CrossChex on demand, and the HR manager edits or
    adds IN/OUT times there regularly. A day marked Absent by an earlier run must
    stop being Absent once a punch appears for it, so every run rebuilds these
    rows from the current check-in data. Only rows this code created
    (custom_is_system_generated) are touched -- anything entered by hand stays.
    """
    leaves = frappe.get_all(
        "Leave Application",
        filters={
            "employee": emp,
            "from_date": [">=", start],
            "to_date": ["<=", end],
            "custom_is_system_generated": 1,
            "docstatus": ["<", 2],
        },
        pluck="name",
    )

    # Submitting a Leave Application makes ERPNext raise its own "On Leave"
    # Attendance, which is not flagged system-generated but still links back to
    # the leave -- so it has to go first or the leave cannot be removed.
    attendance = frappe.get_all(
        "Attendance",
        filters={
            "employee": emp,
            "attendance_date": ["between", [start, end]],
            "custom_is_system_generated": 1,
            "docstatus": ["<", 2],
        },
        pluck="name",
    )
    if leaves:
        attendance += frappe.get_all(
            "Attendance",
            filters={
                "employee": emp,
                "attendance_date": ["between", [start, end]],
                "leave_application": ["in", leaves],
                "docstatus": ["<", 2],
            },
            pluck="name",
        )

    for name in dict.fromkeys(attendance):
        doc = frappe.get_doc("Attendance", name)
        doc.flags.ignore_permissions = True
        if doc.docstatus == 1:
            doc.cancel()
        doc.delete(ignore_permissions=True)

    for leave in leaves:
        doc = frappe.get_doc("Leave Application", leave)
        doc.flags.ignore_permissions = True
        if doc.docstatus == 1:
            doc.cancel()
        doc.delete(ignore_permissions=True)


def process_attendance(emp, start, end, employee_map, holiday_map, checkin_map):
    leave_type = get_employee_leave_type(emp, start, end)

    # Rebuild from scratch so re-running after a check-in re-sync is correct.
    clear_system_generated_attendance(emp, start, end)

    holidays = holiday_map.get(
        employee_map[emp].holiday_list,
        set()
    )

    # 1. Build working days
    working_days = set()
    doj = employee_map[emp].date_of_joining
    current = max(start, doj) if doj else start
    while current <= end:
        if current not in holidays:
            working_days.add(current)
        current = add_days(current, 1)

    # 2. Days with check-in
    present_days = set(checkin_map.get(emp, {}).keys())

    # 3. Days with ANY attendance already marked
    attendance_days = {
        d.attendance_date
        for d in frappe.get_all(
            "Attendance",
            filters={
                "employee": emp,
                "attendance_date": ["between", [start, end]],
                "docstatus": 1
            },
            fields=["attendance_date"]
        )
    }

    # 4. True missing days.
    # Staff exempt from biometric attendance (the owner's driver, anyone without
    # a device) are away from the office as a matter of course, so a day with no
    # punch means nothing. They are Present unless somebody records otherwise:
    # absence is entered by hand and left alone here.
    if employee_map[emp].get("custom_exempt_from_biometric_attendance"):
        missing_days = []
    else:
        missing_days = sorted(
            working_days - present_days - attendance_days
        )

    # 5. Leave limits
    max_leave = get_max_consecutive_leave(leave_type)
    used_leave = count_existing_leave_days(emp, start, end)

    # 6. Apply leave / absent
    for day in missing_days:

        if leave_type or max_leave!=0:
            if used_leave < max_leave:
                create_leave_application(emp, day, leave_type)
                used_leave += 1
            else:
                mark_absent(emp, day)
        else:
            mark_absent(emp, day)

    # 7. Encashment
    if leave_type or max_leave!=0:
        encash = max_leave - used_leave
        if encash > 0:
            create_leave_encashment(emp, end, encash, leave_type)


# =====================================================
# HELPERS
# =====================================================

def get_employee_leave_type(emp, start, end):
    allocation = frappe.db.get_value(
        "Leave Allocation",
        {
            "employee": emp,
            "from_date": ("<=", end),
            "to_date": (">=", start),
            "docstatus": 1
        },
        ["leave_type"],
        as_dict=True
    )

    if not allocation:
        return None

    return allocation.leave_type


def has_attendance_marked(emp, date):
    return frappe.db.exists(
        "Attendance",
        {
            "employee": emp,
            "attendance_date": date,
            "status": ["in", ["Absent", "On Leave"]]
        }
    )

def filter_noise(times):
    clean, last = [], None
    for t in times:
        if not last or (t - last).total_seconds() > 300:
            clean.append(t)
            last = t
    return clean

def shift_bounds(shift, date):
    start = datetime.combine(date, to_time(shift.start_time))
    end = datetime.combine(date, to_time(shift.end_time))
    if end <= start:
        end += timedelta(days=1)
    return start, end

def get_shift_hours(shift):
    s, e = shift_bounds(shift, getdate())
    return (e - s).total_seconds() / 3600

def minutes(delta):
    return int(delta.total_seconds() / 60)

def late_exit_waiver_time(date):
    """Datetime past which leaving is never treated as early-going, or None."""
    cutoff = frappe.db.get_single_value("Calicut Textiles Settings", "early_waiver_after_time")
    if not cutoff:
        return None
    return datetime.combine(date, to_time(cutoff))


def get_hourly_rate(emp):
    """Hourly rate for part-timers, from their Salary Structure Assignment."""
    return flt(
        frappe.db.get_value(
            "Salary Structure Assignment",
            {"employee": emp, "docstatus": 1},
            "custom_hourly_rate",
            order_by="from_date desc",
        )
    )


def get_per_minute_salary(emp, start, end, shift_hours):
    base = frappe.db.get_value(
        "Salary Structure Assignment",
        {"employee": emp, "docstatus": 1},
        "base",
        order_by="from_date desc"
    )
    if not base:
        return 0

    # Per-day salary is always gross/30, independent of the calendar length of
    # the month. Dividing by the real day count made OT and late/early ~3% light
    # in 31-day months and ~7% heavy in February.
    return base / (30 * shift_hours * 60)

# =====================================================
# DOCUMENT CREATORS
# =====================================================
def create_monthly_additional_salary(emp, date, amount, component):
    if amount <= 0:
        return
    if frappe.db.exists(
        "Additional Salary",
        {
            "employee": emp,
            "salary_component": component,
            "payroll_date": date,
            "docstatus": 1
        }
    ):
        return
    doc = frappe.new_doc("Additional Salary")
    doc.employee = emp
    doc.salary_component = component
    doc.amount = amount
    doc.payroll_date = date
    doc.custom_is_system_generated = 1
    doc.docstatus = 1
    doc.insert(ignore_permissions=True)

def create_monthly_overtime(emp, date, minutes, amount, component, is_overtime=True):
    existing = frappe.get_all(
        "Additional Salary",
        filters={
            "employee": emp,
            "salary_component": component,
            "payroll_date": date,
            "docstatus": 1
        }
    )
    if existing:
        existing_doc = frappe.get_doc("Additional Salary", existing[0].name)
        if float(existing_doc.amount) == float(amount):
            return
        # Bypass permission checks
        existing_doc.flags.ignore_permissions = True
        existing_doc.cancel()

        existing_doc.delete(ignore_permissions=True)
    if amount <= 0:
        return
    doc = frappe.new_doc("Additional Salary")
    doc.employee = emp  # was `emp,` -- the trailing comma made this a tuple
    doc.custom_is_system_generated = 1
    doc.salary_component = component
    # Flag which kind of row this is. Late/Early was previously also stamped
    # custom_is_overtime=1, and custom_is_late_early was never set at all.
    if is_overtime:
        doc.custom_is_overtime = 1
        doc.custom_ot_min = minutes
    else:
        doc.custom_is_late_early = 1
        doc.custom_late_early_min = minutes
    doc.amount = amount
    doc.payroll_date = date
    doc.docstatus = 1
    doc.insert(ignore_permissions=True)

# =====================================================
# LEAVE
# =====================================================

def count_existing_leave_days(emp, start, end):
    start = getdate(start)
    end = getdate(end)

    leaves = frappe.db.get_all(
        "Leave Application",
        filters={
            "employee": emp,
            "docstatus": 1,
            "from_date": ["<=", end],
            "to_date": [">=", start],
        },
        fields=["from_date", "to_date"]
    )

    total_days = 0

    for leave in leaves:
        leave_start = max(getdate(leave.from_date), start)
        leave_end = min(getdate(leave.to_date), end)

        # +1 because both dates are inclusive
        days = (leave_end - leave_start).days + 1
        total_days += days

    return total_days


def create_leave_application(emp, date, leave_type):
    doc = frappe.new_doc("Leave Application")
    doc.employee = emp
    doc.leave_type = leave_type
    doc.from_date = date
    doc.to_date = date
    doc.custom_is_system_generated = 1
    doc.status = "Approved"
    doc.docstatus = 1
    doc.insert(ignore_permissions=True)

def mark_absent(emp, date):
    if frappe.db.exists(
        "Attendance",
        {"employee": emp, "attendance_date": date}
    ):
        return
    doc = frappe.new_doc("Attendance")
    doc.employee = emp
    doc.attendance_date = date
    doc.status = "Absent"
    doc.custom_is_system_generated = 1
    doc.docstatus = 1
    doc.insert(ignore_permissions=True)

def create_leave_encashment(emp, date, days, leave_type):
    component = get_leave_encashment_component(leave_type)
    existing = frappe.get_all(
        "Additional Salary",
        filters={
            "employee": emp,
            "salary_component": component,
            "docstatus": 1
        }
    )
    daily = frappe.db.get_value(
        "Salary Structure Assignment",
        {"employee": emp, "docstatus": 1},
        "custom_leave_encashment_amount_per_day",
        order_by="from_date desc"
    )
    if not daily:
        return
    amount = round(daily * days, 2)
    if existing:
        existing_doc = frappe.get_doc("Additional Salary", existing[0].name)
        if float(existing_doc.amount) == float(amount):
            return
        # Bypass permission checks
        existing_doc.flags.ignore_permissions = True
        existing_doc.cancel()

        existing_doc.delete(ignore_permissions=True)
        return

    doc = frappe.new_doc("Additional Salary")
    doc.employee = emp
    doc.salary_component = component
    doc.amount = amount
    doc.payroll_date = date
    doc.custom_is_system_generated = 1
    doc.docstatus = 1
    doc.insert(ignore_permissions=True)

def to_time(value):
    if isinstance(value, timedelta):
        secs = int(value.total_seconds())
        return time(secs // 3600, (secs % 3600) // 60, secs % 60)
    return value

@frappe.whitelist()
def cancell_additonal_salary(doc, method):
    for row in doc.employees:
        additional_salaries = frappe.get_all("Additional Salary",
            filters={
                "employee": row.employee,
                "payroll_date": doc.end_date,
                "custom_is_system_generated": 1,
                "docstatus": 1
            },
            fields=["name"]
        )
        for sal in additional_salaries:
            additional_salary_doc = frappe.get_doc("Additional Salary", sal.name)
            # Bypass permission checks
            additional_salary_doc.flags.ignore_permissions = True
            additional_salary_doc.cancel()

            additional_salary_doc.delete(ignore_permissions=True)

from hrms.payroll.doctype.payroll_entry.payroll_entry import PayrollEntry
from hrms.payroll.doctype.payroll_entry.payroll_entry import log_payroll_failure, get_existing_salary_slips
from frappe import _

class CustomPayrollEntry(PayrollEntry):
    @frappe.whitelist()
    def create_salary_slips(self):
        """
        Creates salary slip for selected employees if already not created
        """
        self.check_permission("write")
        employees = [emp.employee for emp in self.employees]

        if employees:
            args = frappe._dict(
                {
                    "salary_slip_based_on_timesheet": self.salary_slip_based_on_timesheet,
                    "payroll_frequency": self.payroll_frequency,
                    "start_date": self.start_date,
                    "end_date": self.end_date,
                    "company": self.company,
                    "posting_date": self.posting_date,
                    "deduct_tax_for_unclaimed_employee_benefits": self.deduct_tax_for_unclaimed_employee_benefits,
                    "deduct_tax_for_unsubmitted_tax_exemption_proof": self.deduct_tax_for_unsubmitted_tax_exemption_proof,
                    "payroll_entry": self.name,
                    "exchange_rate": self.exchange_rate,
                    "currency": self.currency,
                }
            )
            if len(employees) > 30 or frappe.flags.enqueue_payroll_entry:
                self.db_set("status", "Queued")
                frappe.enqueue(
                    create_salary_slips_for_employees,
                    timeout=3000,
                    employees=employees,
                    args=args,
                    publish_progress=False,
                )
                frappe.msgprint(
                    _("Salary Slip creation is queued. It may take a few minutes"),
                    alert=True,
                    indicator="blue",
                )
            else:
                create_salary_slips_for_employees(employees, args, publish_progress=False)
                # since this method is called via frm.call this doc needs to be updated manually
                self.reload()


def create_salary_slips_for_employees(employees, args, publish_progress=True):
    payroll_entry = frappe.get_cached_doc("Payroll Entry", args.payroll_entry)

    try:
        salary_slips_exist_for = get_existing_salary_slips(employees, args)
        count = 0

        employees = list(set(employees) - set(salary_slips_exist_for))
        for emp in employees:
            args.update({"doctype": "Salary Slip", "employee": emp})
            doc = frappe.get_doc(args).insert()
            doc.save()

            count += 1
            if publish_progress:
                frappe.publish_progress(
                    count * 100 / len(employees),
                    title=_("Creating Salary Slips..."),
                )

        payroll_entry.db_set({"status": "Submitted", "salary_slips_created": 1, "error_message": ""})

        if salary_slips_exist_for:
            frappe.msgprint(
                _(
                    "Salary Slips already exist for employees {}, and will not be processed by this payroll."
                ).format(frappe.bold(", ".join(emp for emp in salary_slips_exist_for))),
                title=_("Message"),
                indicator="orange",
            )

    except Exception as e:
        frappe.db.rollback()
        log_payroll_failure("creation", payroll_entry, e)

    finally:
        frappe.db.commit()  # nosemgrep
        frappe.publish_realtime("completed_salary_slip_creation", user=frappe.session.user)