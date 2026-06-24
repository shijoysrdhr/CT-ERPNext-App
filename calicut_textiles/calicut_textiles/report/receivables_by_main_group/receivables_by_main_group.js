// Copyright (c) 2026, Calicut Textiles and contributors
// For license information, please see license.txt

frappe.query_reports["Receivables by Main Group"] = {
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
			fieldname: "report_date",
			label: __("As on Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
	],
};
