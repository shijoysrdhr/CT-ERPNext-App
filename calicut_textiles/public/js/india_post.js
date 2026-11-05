// India Post shipping on submitted Sales Invoices and Delivery Notes.
// Server side: calicut_textiles/calicut_textiles/india_post.py
(() => {
	const M = "calicut_textiles.calicut_textiles.india_post.";
	const GROUP = __("India Post");
	const COLORS = { Booked: "blue", "In Transit": "orange", Delivered: "green", Returned: "red", Failed: "red", Cancelled: "gray" };
	const REBOOKABLE = ["", null, undefined, "Failed", "Cancelled"];
	const PRODUCTS = [
		{ value: "SP", label: __("Speed Post (document under 500 g, parcel above)") },
		{ value: "BUSINESS_PARCEL", label: __("Business Parcel") },
		{ value: "24_SPEEDPOST_DOC", label: __("24-hour Speed Post — Document") },
		{ value: "48_SPEEDPOST_DOC", label: __("48-hour Speed Post — Document") },
		{ value: "24_SPP_PARSPL", label: __("24-hour Speed Post — Parcel (up to 5 kg)") },
	];
	// Remember the last parcel size per user — most parcels from one counter look alike.
	const LAST_KEY = "ct_india_post_last_parcel";

	function lastParcel() {
		try { return JSON.parse(localStorage.getItem(LAST_KEY)) || {}; } catch { return {}; }
	}
	function saveParcel(p) {
		try { localStorage.setItem(LAST_KEY, JSON.stringify(p)); } catch { /* private window */ }
	}

	function refresh(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.is_return) return;
		const status = frm.doc.custom_ip_status;
		const awb = frm.doc.custom_ip_awb;

		if (awb && !REBOOKABLE.includes(status)) {
			const last = frm.doc.custom_ip_last_event ? ` · ${frappe.utils.escape_html(frm.doc.custom_ip_last_event)}` : "";
			frm.dashboard.set_headline_alert(
				`${__("India Post")} <b>${frappe.utils.escape_html(awb)}</b> — ${__(status)}${last}`,
				COLORS[status] || "blue"
			);
			frm.add_custom_button(__("Print Label"), () => printLabel(frm), GROUP);
			frm.add_custom_button(__("Refresh Tracking"), () => refreshTracking(frm), GROUP);
			frm.add_custom_button(__("Copy AWB"), () => frappe.utils.copy_to_clipboard(awb), GROUP);
		} else {
			if (status === "Failed") {
				frm.dashboard.set_headline_alert(__("India Post booking failed — see the comments below, fix and book again."), "red");
			}
			frm.add_custom_button(__("Book India Post"), () => openDialog(frm), GROUP);
		}
	}

	function printLabel(frm) {
		frappe.call({
			method: M + "get_label",
			args: { doctype: frm.doctype, name: frm.docname },
			freeze: true,
			freeze_message: __("Fetching label…"),
		}).then((r) => {
			if (r.message) window.open(r.message, "_blank");
			frm.reload_doc();
		});
	}

	function refreshTracking(frm) {
		frappe.call({
			method: M + "refresh_status",
			args: { doctype: frm.doctype, name: frm.docname },
			freeze: true,
		}).then(() => frm.reload_doc());
	}

	function readForm(d) {
		const v = d.get_values(true) || {};
		return {
			receiver: {
				name: v.receiver_name, mobile: v.mobile,
				addressLine1: v.address_line1, addressLine2: v.address_line2, addressLine3: v.address_line3,
				city: v.city, state: v.state, pincode: v.pincode,
			},
			parcel: {
				articleType: v.product,
				weight: cint(v.weight),
				length: flt(v.length), breadth: flt(v.breadth), height: flt(v.height),
				codValue: v.cod ? flt(v.cod_value) : 0,
				insuranceValue: flt(v.insurance_value),
				orderAmount: flt(d.order_amount),
			},
		};
	}

	function showMessage(d, html, indicator) {
		d.fields_dict.result.$wrapper.html(
			`<div class="alert alert-${indicator || "info"}" style="margin-top:8px">${html}</div>`
		);
	}

	function showRates(d, data) {
		const quotes = data.quotes || [];
		if (!quotes.length) {
			const why = (data.failures || []).map((f) => `<li>${frappe.utils.escape_html(f.articleType)}: ${frappe.utils.escape_html(f.error)}</li>`).join("");
			return showMessage(d, `${__("No rates returned.")}<ul>${why}</ul>`, "warning");
		}
		const rows = quotes.map((q) => `
			<tr>
				<td>${frappe.utils.escape_html(q.articleType || q.productCode || "")}</td>
				<td class="text-right">${q.chargeableWeight ?? ""} g</td>
				<td class="text-right"><b>${format_currency(q.finalAmount, "INR")}</b></td>
				<td>${q.assuredDeliveryDay ? frappe.datetime.str_to_user(q.assuredDeliveryDay) : ""}</td>
			</tr>`).join("");
		d.fields_dict.result.$wrapper.html(`
			<table class="table table-bordered table-sm" style="margin-top:8px">
				<thead><tr><th>${__("Product")}</th><th class="text-right">${__("Charged weight")}</th>
				<th class="text-right">${__("Total incl. GST")}</th><th>${__("Assured by")}</th></tr></thead>
				<tbody>${rows}</tbody>
			</table>`);
	}

	function openDialog(frm) {
		frappe.call({
			method: M + "get_booking_defaults",
			args: { doctype: frm.doctype, name: frm.docname },
		}).then((r) => {
			const def = r.message || {};
			const rc = def.receiver || {};
			const last = lastParcel();

			const d = new frappe.ui.Dialog({
				title: __("Book India Post — {0}", [frm.docname]),
				size: "large",
				fields: [
					{ fieldtype: "Section Break", label: __("Deliver to") },
					{ fieldname: "receiver_name", fieldtype: "Data", label: __("Name"), reqd: 1, default: rc.name },
					{ fieldname: "mobile", fieldtype: "Data", label: __("Mobile"), reqd: 1, default: rc.mobile,
					  description: __("10 digits, starting 6–9") },
					{ fieldtype: "Column Break" },
					{ fieldname: "pincode", fieldtype: "Data", label: __("PIN code"), reqd: 1, default: rc.pincode },
					{ fieldname: "city", fieldtype: "Data", label: __("City"), reqd: 1, default: rc.city },
					{ fieldname: "state", fieldtype: "Data", label: __("State"), default: rc.state },
					{ fieldtype: "Section Break" },
					{ fieldname: "address_line1", fieldtype: "Data", label: __("Address line 1"), reqd: 1, length: 240,
					  default: rc.addressLine1, description: rc.addressName ? __("From address {0}", [rc.addressName]) : __("No address on this document — type it in") },
					{ fieldname: "address_line2", fieldtype: "Data", label: __("Address line 2"), default: rc.addressLine2 },
					{ fieldname: "address_line3", fieldtype: "Data", label: __("Address line 3"), default: rc.addressLine3 },

					{ fieldtype: "Section Break", label: __("Parcel") },
					{ fieldname: "product", fieldtype: "Select", label: __("Product"), reqd: 1,
					  options: PRODUCTS, default: last.product || "SP" },
					{ fieldname: "weight", fieldtype: "Int", label: __("Weight (grams)"), reqd: 1, default: last.weight,
					  description: __("Weigh the packed parcel. Whole grams.") },
					{ fieldtype: "Column Break" },
					{ fieldname: "length", fieldtype: "Float", label: __("Length (cm)"), reqd: 1, default: last.length || 30 },
					{ fieldname: "breadth", fieldtype: "Float", label: __("Breadth (cm)"), reqd: 1, default: last.breadth || 21 },
					{ fieldname: "height", fieldtype: "Float", label: __("Height (cm)"), reqd: 1, default: last.height || 5 },
					{ fieldtype: "Column Break" },
					{ fieldname: "cod", fieldtype: "Check", label: __("Cash on delivery"), default: 0 },
					{ fieldname: "cod_value", fieldtype: "Currency", label: __("COD amount"), depends_on: "cod",
					  default: def.outstanding || def.orderAmount },
					{ fieldname: "insurance_value", fieldtype: "Currency", label: __("Insure for (optional)"), default: 0,
					  description: __("Costs extra — leave 0 to skip") },

					{ fieldtype: "Section Break" },
					{ fieldname: "result", fieldtype: "HTML" },
				],
				secondary_action_label: __("Get Rates"),
				secondary_action() {
					const { receiver, parcel } = readForm(d);
					if (!parcel.weight || !receiver.pincode) {
						return showMessage(d, __("Enter the PIN code and weight first."), "warning");
					}
					showMessage(d, __("Asking India Post for rates…"));
					frappe.call({
						method: M + "get_rates",
						args: { doctype: frm.doctype, name: frm.docname, receiver, parcel },
					}).then((res) => showRates(d, res.message || {}))
					  .catch(() => showMessage(d, __("Could not get rates."), "danger"));
				},
				primary_action_label: __("Book"),
				primary_action() {
					if (!d.get_values()) return; // shows missing mandatory fields
					const { receiver, parcel } = readForm(d);
					saveParcel({ product: parcel.articleType, weight: parcel.weight, length: parcel.length, breadth: parcel.breadth, height: parcel.height });
					d.get_primary_btn().prop("disabled", true);
					frappe.call({
						method: M + "book",
						args: { doctype: frm.doctype, name: frm.docname, receiver, parcel },
						freeze: true,
						freeze_message: __("Booking with India Post…"),
					}).then((res) => {
						const out = res.message || {};
						if (!out.ok) {
							d.get_primary_btn().prop("disabled", false);
							const list = (out.errors || []).map((e) => `<li>${frappe.utils.escape_html(e)}</li>`).join("");
							const head = out.stage === "validation"
								? __("Please fix these before booking:")
								: __("India Post rejected the booking:");
							return showMessage(d, `<b>${head}</b><ul style="margin:6px 0 0">${list}</ul>`, "danger");
						}
						d.hide();
						const s = out.shipment || {};
						frappe.show_alert({ message: __("Booked — AWB {0}", [s.awb]), indicator: "green" }, 7);
						if (out.labelUrl) window.open(out.labelUrl, "_blank");
						else if (out.labelError) frappe.msgprint(__("Booked, but the label could not be fetched yet: {0}. Use India Post → Print Label.", [out.labelError]));
						frm.reload_doc();
					}).catch(() => d.get_primary_btn().prop("disabled", false));
				},
			});
			d.order_amount = def.orderAmount;
			d.show();
		});
	}

	for (const doctype of ["Sales Invoice", "Delivery Note"]) {
		frappe.ui.form.on(doctype, { refresh });
	}
})();
