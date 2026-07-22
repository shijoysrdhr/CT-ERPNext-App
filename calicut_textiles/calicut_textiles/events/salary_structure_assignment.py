import frappe
from frappe import _
from frappe.utils import flt

DEFAULT_PAYABLE_DAYS = 30


def validate_encashment_amount(doc, method):
    # Part-timers are paid purely by the hour, so they legitimately have no
    # monthly gross. Everyone else must have one.
    is_part_time = frappe.db.get_value("Employee", doc.employee, "employment_type") == "Part-time"

    if not doc.base and not is_part_time:
        frappe.throw(_("Base amount is required"))

    if is_part_time and not doc.get("custom_hourly_rate"):
        frappe.throw(_("Hourly Rate is required for part-time employees"))

    # The base covers `custom_payable_days` days -- 30 for a normal full month,
    # fewer for staff paid for only part of it (an employee shared with another
    # company). Everything per-day derives from this.
    payable_days = flt(doc.get("custom_payable_days")) or DEFAULT_PAYABLE_DAYS
    doc.custom_leave_encashment_amount_per_day = (doc.base or 0) / payable_days
