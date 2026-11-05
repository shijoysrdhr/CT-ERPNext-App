// Copyright (c) 2026, Calicut Textiles and contributors
// For license information, please see license.txt

frappe.query_reports["India Post Shipments"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("Booked From"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		},
		{
			fieldname: "to_date",
			label: __("Booked To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nBooked\nIn Transit\nDelivered\nReturned\nFailed\nCancelled",
		},
		{
			fieldname: "document_type",
			label: __("Document Type"),
			fieldtype: "Select",
			options: "\nSales Invoice\nDelivery Note",
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data) {
			const color = { Booked: "blue", "In Transit": "orange", Delivered: "green", Returned: "red", Failed: "red" }[data.status];
			if (color) value = `<span class="indicator-pill ${color}">${value}</span>`;
		}
		return value;
	},
};
