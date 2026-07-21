import frappe
from frappe import _


def validate_encashment_amount(doc, method):
    # Part-timers are paid purely by the hour, so they legitimately have no
    # monthly gross. Everyone else must have one.
    is_part_time = frappe.db.get_value("Employee", doc.employee, "employment_type") == "Part-time"

    if not doc.base and not is_part_time:
        frappe.throw(_("Base amount is required"))

    if is_part_time and not doc.get("custom_hourly_rate"):
        frappe.throw(_("Hourly Rate is required for part-time employees"))

    doc.custom_leave_encashment_amount_per_day = (doc.base or 0) / 30
