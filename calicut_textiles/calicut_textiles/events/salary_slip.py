import frappe
from frappe.utils import flt, get_last_day, getdate


DEFAULT_PAYABLE_DAYS = 30


def get_base(employee, end_date):
    """Monthly gross from the employee's latest Salary Structure Assignment."""
    return flt(
        frappe.db.get_value(
            "Salary Structure Assignment",
            {"employee": employee, "from_date": ["<=", end_date], "docstatus": 1},
            "base",
            order_by="from_date desc",
        )
    )


def get_payable_days(employee, end_date=None):
    """Days the monthly gross covers -- the divisor for per-day salary.

    30 for everyone on a normal full month. Lower for staff paid for only part
    of it, such as an employee shared with another company: SARATH is paid
    9,000 by Calicut Textiles for 15 days, so a day of his costs 9,000/15.
    """
    days = frappe.db.get_value(
        "Salary Structure Assignment",
        {"employee": employee, "from_date": ["<=", end_date], "docstatus": 1}
        if end_date
        else {"employee": employee, "docstatus": 1},
        "custom_payable_days",
        order_by="from_date desc",
    )
    return flt(days) or DEFAULT_PAYABLE_DAYS


def get_late_early_amount(employee, start_date, end_date):
    """Late/Early deduction raised for this month by the Payroll Entry."""
    component = frappe.db.get_single_value("Calicut Textiles Settings", "early_component")
    if not component:
        return 0.0

    rows = frappe.get_all(
        "Additional Salary",
        filters={
            "employee": employee,
            "salary_component": component,
            "payroll_date": ["between", [start_date, end_date]],
            "docstatus": 1,
        },
        pluck="amount",
    )
    return sum(flt(a) for a in rows)


def set_deducted_gross(doc):
    """Set the ESI/PF base fields that the salary structure formulas read.

    Mirrors the payroll workbook:
        per day       = gross / payable days (30 unless the employee is shared)
        LOP           = unpaid absent days x per day
        ESI salary  T = gross - (LOP + late/early)
        PF salary     = T * 0.625 + (T * 0.625) * 0.40   ->  T * 0.875

    Returns True if the figures moved, so the caller knows to re-total the slip.
    """
    if not (doc.employee and doc.start_date):
        return False

    base = get_base(doc.employee, doc.end_date)
    if not base:
        return False

    per_day = base / get_payable_days(doc.employee, doc.end_date)
    # absent_days is 0 until get_working_days_details() has run, so this is a
    # seed value on before_insert and the real figure on validate.
    unpaid_days = flt(doc.absent_days) + flt(doc.leave_without_pay)
    lop = unpaid_days * per_day
    late_early = get_late_early_amount(doc.employee, doc.start_date, doc.end_date)

    deducted_gross = base - lop - late_early
    deducted_basic = deducted_gross * 0.625
    # DA is 40% of BASIC, not 40% of gross. The previous `deducted_gross * 40 / 100`
    # made the PF base 1.025 x T instead of 0.875 x T and over-deducted PF.
    deducted_da = deducted_basic * 0.40

    changed = flt(doc.custom_deducted_gross, 2) != flt(deducted_gross, 2)

    doc.custom_deducted_gross = deducted_gross
    doc.custom_deducted_basic = deducted_basic
    doc.custom_deducted_da = deducted_da
    doc.custom_deducted_per_day = per_day

    return changed


def before_save(doc, method=None):
    """Seed the ESI/PF base before the first formula evaluation."""
    set_deducted_gross(doc)


@frappe.whitelist()
def add_pf_esi_deduction(doc, method=None):
    """Recompute ESI/PF once the real attendance figures are known.

    Runs after Salary Slip.validate(), by which point get_working_days_details()
    has set absent_days and the structure formulas have been evaluated once against
    the seed figures. Re-deriving the base and re-running calculate_net_pay() lets
    each structure's own ESI/PF formulas produce the final numbers -- so employees
    on the "Basic" / "w/o PF" / "w/o WF" structures correctly get no such rows.

    The previous version appended PF and ESI to `earnings` (paying them out rather
    than deducting them) and applied them to every employee regardless of structure.

    calculate_net_pay() updates component rows in place (see update_component_row),
    so the second pass re-values rows rather than duplicating them.
    """
    if not doc.salary_structure:
        return

    if set_deducted_gross(doc):
        doc.calculate_net_pay()


@frappe.whitelist()
def calculate_deducted_gross(employee, start_date):
    """Kept for anything calling this directly."""
    end_date = get_last_day(getdate(start_date))
    base = get_base(employee, end_date)
    return base - get_late_early_amount(employee, getdate(start_date), end_date)
