// Copyright (c) 2026, Calicut Textiles and contributors
// For license information, please see license.txt

const IP = "calicut_textiles.calicut_textiles.india_post.";

frappe.ui.form.on("India Post Settings", {
	refresh(frm) {
		show_stock(frm);

		frm.add_custom_button(__("Test connection"), () => {
			frappe.call({ method: IP + "test_connection", freeze: true, freeze_message: __("Logging in to India Post…") })
				.then((r) => {
					const m = r.message || {};
					const rows = (m.offices || []).map((o) =>
						`<tr><td>${frappe.utils.escape_html(String(o.office_id))}</td><td>${frappe.utils.escape_html(o.office_name || "")}</td>
						<td>${o.bookable ? "✓" : ""}</td></tr>`).join("");
					frappe.msgprint({
						title: __("Connected to India Post"),
						indicator: m.booking_office_found ? "green" : "orange",
						message: `<p>${__("Login works.")} ${m.booking_office_found
							? __("Your booking office ID was found under its PIN code.")
							: __("Your booking office ID was NOT found under its PIN code — check it.")}</p>
							<table class="table table-bordered table-sm"><thead><tr><th>${__("Office ID")}</th><th>${__("Office")}</th><th>${__("Bookable")}</th></tr></thead>
							<tbody>${rows}</tbody></table>`,
					});
				});
		});

		frm.add_custom_button(__("Add AWB series"), () => {
			const d = new frappe.ui.Dialog({
				title: __("Add the AWB numbers India Post issued"),
				fields: [
					{ fieldname: "from_awb", fieldtype: "Data", label: __("First AWB"), reqd: 1,
					  description: __("As India Post wrote it, e.g. ET21433001XIN") },
					{ fieldname: "to_awb", fieldtype: "Data", label: __("Last AWB"), reqd: 1,
					  description: __("e.g. ET21434000XIN") },
					{ fieldname: "note", fieldtype: "Data", label: __("Note"), description: __("e.g. Production series, letter dated …") },
				],
				primary_action_label: __("Add"),
				primary_action(v) {
					frappe.call({ method: IP + "add_awb_series", args: v, freeze: true }).then((r) => {
						const m = r.message || {};
						d.hide();
						frappe.msgprint(__("Added {0} AWB numbers ({1} – {2}). {3} were already there.",
							[m.added, m.first, m.last, m.already_there]));
						show_stock(frm);
					});
				},
			});
			d.show();
		});

		frm.add_custom_button(__("AWB stock"), () => frappe.set_route("List", "India Post AWB"), __("View"));
		frm.add_custom_button(__("Shipments"), () => frappe.set_route("List", "India Post Shipment"), __("View"));
	},
});

function show_stock(frm) {
	frappe.call({ method: IP + "get_awb_stock" }).then((r) => {
		const s = r.message || {};
		const color = s.low ? "var(--red-600)" : "var(--green-600)";
		frm.fields_dict.awb_stock.$wrapper.html(`
			<div style="font-size:13px;line-height:1.8">
				<div style="font-size:20px;font-weight:600;color:${color}">${s.available ?? 0} ${__("available")}</div>
				<div>${__("Used")}: ${s.used ?? 0} · ${__("On hold")}: ${s.allocated ?? 0} · ${__("Void")}: ${s.void ?? 0}</div>
				${s.low ? `<div style="color:${color}">${__("Below the warning level of {0} — ask India Post for a new series.", [s.threshold])}</div>` : ""}
			</div>`);
	});
}
