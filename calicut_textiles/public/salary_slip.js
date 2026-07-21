// The Deducted Gross / Basic / DA figures are owned by the server:
// calicut_textiles.calicut_textiles.events.salary_slip.set_deducted_gross()
// runs on before_insert and again on validate, once absent_days is known.
//
// This form previously recomputed them client-side on every employee/start_date/
// end_date change, overwriting the server's numbers with wrong ones:
//   - DA was gross * 40% instead of BASIC * 40%, inflating the PF base
//   - leave_without_pay (a DAY COUNT) was subtracted from a RUPEE amount
//   - per-day used payment_days rather than the fixed 30
// Leave the fields to the server; they refresh on save.

frappe.ui.form.on('Salary Slip', {
    // intentionally empty
});
