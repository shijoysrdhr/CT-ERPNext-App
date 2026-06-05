// Copyright (c) 2026, Calicut Textiles and contributors
// For license information, please see license.txt

frappe.query_reports["Batch Repack Worklist"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "sales_invoice",
			label: __("Sales Invoice (Draft)"),
			fieldtype: "Link",
			options: "Sales Invoice",
			get_query: () => ({ filters: { docstatus: 0 } }),
		},
		{
			fieldname: "only_commented",
			label: __("Only Commented"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "commented" && data && data.commented === "Yes") {
			return `<span style="color:var(--green-600);font-weight:600">${value}</span>`;
		}
		if (data && data.action === "Material Receipt") {
			value = `<span style="color:var(--red-600)">${value}</span>`;
		} else if (data && data.action === "Transfer") {
			value = `<span style="color:var(--blue-600)">${value}</span>`;
		}
		return value;
	},

	onload: function (report) {
		const run = (action, method, label, indicator, confirmExtra) => {
			const rows = (report.data || []).filter((r) => r.action === action && flt(r.qty) > 0);
			if (!rows.length) {
				frappe.msgprint(__("No '{0}' rows in the current results.", [action]));
				return;
			}
			const totalQty = rows.reduce((s, r) => s + flt(r.qty), 0);
			let msg = __("Create back-dated {0}(s) for {1} line(s) totalling {2} units?", [label, rows.length, totalQty]);
			if (confirmExtra) msg += "<br><br>" + confirmExtra;

			frappe.confirm(msg, function () {
				frappe.call({
					method: method,
					args: { rows: JSON.stringify(rows) },
					freeze: true,
					freeze_message: __("Creating {0}(s)…", [label]),
					callback: function (r) {
						const res = r.message || { created: [], skipped: [] };
						let html = "";
						if (res.created.length) {
							html += __("✅ Created {0} draft {1}(s): {2}", [
								res.created.length,
								label,
								res.created.map((n) => `<a href="/app/stock-entry/${n}">${n}</a>`).join(", "),
							]) + "<br>" + __("Review, submit them, then submit your invoices.");
						}
						if (res.skipped.length) {
							if (html) html += "<br><br>";
							html += __("⚠️ Skipped {0} invoice(s):", [res.skipped.length]) +
								"<ul>" +
								res.skipped
									.map((s) => `<li><b>${s.si}</b>: ${frappe.utils.escape_html(s.reason || "")}</li>`)
									.join("") +
								"</ul>";
						}
						frappe.msgprint({
							title: __("{0} result", [label]),
							indicator: res.skipped.length ? "orange" : indicator,
							message: html || __("Nothing to create."),
						});
					},
				});
			});
		};

		report.page.add_inner_button(__("Create Transfer"), () =>
			run("Transfer", "calicut_textiles.api.batch_repack.create_material_transfer", __("Material Transfer"), "blue")
		);
		report.page.add_inner_button(__("Create Repack"), () =>
			run("Repack", "calicut_textiles.api.batch_repack.create_repack", __("Repack Stock Entry"), "green")
		);
		report.page.add_inner_button(__("Create Material Receipt"), () =>
			run(
				"Material Receipt",
				"calicut_textiles.api.batch_repack.create_material_receipt",
				__("Material Receipt"),
				"orange",
				__("This CREATES new stock — use only when goods genuinely arrived but weren't received yet. <b>Check the valuation rate on the draft before submitting.</b>")
			)
		);
	},
};
