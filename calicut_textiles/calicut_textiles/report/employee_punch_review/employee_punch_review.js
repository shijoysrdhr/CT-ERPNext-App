// Copyright (c) 2026, sammish and contributors
// For license information, please see license.txt

frappe.query_reports["Employee Punch Review"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
			get_query: () => ({ filters: { status: "Active" } }),
		},
		{
			fieldname: "only_issues",
			label: __("Only Show Days Needing Attention"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "min_break_minutes",
			label: __("Ignore Breaks Under (min)"),
			fieldtype: "Int",
			default: 0,
		},
		{
			fieldname: "include_exempt",
			label: __("Include Staff Exempt From Biometric Attendance"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.fieldname === "issue" && data) {
			if (data.issue === "Missing Punch") {
				value = `<span class="indicator-pill red">${__("Missing Punch")}</span>`;
			} else if (data.issue === "Break Recorded") {
				value = `<span class="indicator-pill orange">${__("Break Recorded")}</span>`;
			}
		}

		// An odd punch count is the cause, so make it read as the cause.
		if (column.fieldname === "punches" && data && data.punches % 2) {
			value = `<span style="color: var(--red-500); font-weight: 600">${data.punches}</span>`;
		}

		if (column.fieldname === "break_minutes" && data && data.break_minutes > 0) {
			value = `<span style="color: var(--orange-500)">${value}</span>`;
		}

		return value;
	},
};
