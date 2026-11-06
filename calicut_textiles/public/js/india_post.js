// India Post shipping on submitted Sales Invoices and Delivery Notes.
// Server side: calicut_textiles/calicut_textiles/india_post.py (talks to India Post directly).
(() => {
	const M = "calicut_textiles.calicut_textiles.india_post.";
	const GROUP = __("India Post");
	const COLORS = { Booked: "blue", "In Transit": "orange", Delivered: "green", Returned: "red", Failed: "red", Cancelled: "gray" };
	const REBOOKABLE = ["", null, undefined, "Failed", "Cancelled"];
	// The 24/48-hour products are not bookable from Kozhikode, so only these two.
	const PRODUCTS = [
		{ value: "SP", label: __("Speed Post (up to 500 g = document, max 2 cm thick)") },
		{ value: "BUSINESS_PARCEL", label: __("Business Parcel") },
	];
	// Remember the last parcel size per user — most parcels from one counter look alike.
	const LAST_KEY = "ct_india_post_last_parcel";
	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));

	function lastParcel() {
		try { return JSON.parse(localStorage.getItem(LAST_KEY)) || {}; } catch { return {}; }
	}
	function saveParcel(p) {
		try { localStorage.setItem(LAST_KEY, JSON.stringify(p)); } catch { /* private window */ }
	}

	function refresh(frm) {
		if (!frappe.boot.india_post_enabled) return; // not enabled, or not in the pilot list
		if (frm.doc.docstatus !== 1 || frm.doc.is_return) return;
		const status = frm.doc.custom_ip_status;
		const awb = frm.doc.custom_ip_awb;

		if (awb && !REBOOKABLE.includes(status)) {
			const last = frm.doc.custom_ip_last_event ? ` · ${esc(frm.doc.custom_ip_last_event)}` : "";
			frm.dashboard.set_headline_alert(
				`${__("India Post")} <b>${esc(awb)}</b> — ${__(status)}${last}`,
				COLORS[status] || "blue"
			);
			frm.add_custom_button(__("Print Label"), () => printLabel(frm), GROUP);
			frm.add_custom_button(__("Refresh Tracking"), () => refreshTracking(frm), GROUP);
			frm.add_custom_button(__("Copy AWB"), () => frappe.utils.copy_to_clipboard(awb), GROUP);
			if (status === "Booked") frm.add_custom_button(__("Cancel Booking"), () => cancelBooking(frm), GROUP);
		} else {
			if (status === "Failed") {
				frm.dashboard.set_headline_alert(__("India Post booking failed — see the comments below, fix and book again."), "red");
			} else if (status === "Cancelled") {
				frm.dashboard.set_headline_alert(__("India Post booking cancelled — never use the old label. Book again for a new AWB."), "gray");
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

	function cancelBooking(frm) {
		frappe.prompt(
			[{ fieldname: "reason", fieldtype: "Data", label: __("Why? (e.g. wrong address)"), reqd: 1 }],
			(v) => frappe.call({
				method: M + "cancel_booking",
				args: { doctype: frm.doctype, name: frm.docname, reason: v.reason },
				freeze: true,
				freeze_message: __("Cancelling…"),
			}).then(() => {
				frappe.show_alert({ message: __("Booking cancelled. Throw away the old label; book again for a new AWB."), indicator: "orange" }, 8);
				frm.reload_doc();
			}),
			__("Cancel the India Post booking for {0}? The invoice is not touched.", [frm.docname]),
			__("Cancel Booking")
		);
	}

	function readForm(d) {
		const v = d.get_values(true) || {};
		return {
			receiver: {
				name: v.receiver_name, mobile: v.mobile,
				address_line1: v.address_line1, address_line2: v.address_line2, address_line3: v.address_line3,
				city: v.city, state: v.state, pincode: v.pincode,
			},
			parcel: {
				article_type: v.product,
				weight: cint(v.weight),
				length: flt(v.length), breadth: flt(v.breadth), height: flt(v.height),
				cod_value: v.cod ? flt(v.cod_value) : 0,
				insurance_value: flt(v.insurance_value),
			},
		};
	}

	function showMessage(d, html, indicator) {
		d.fields_dict.result.$wrapper.html(
			`<div class="alert alert-${indicator || "info"}" style="margin-top:8px">${html}</div>`
		);
	}

	function failureList(failures) {
		const items = (failures || []).map((f) => {
			const who = f.label || f.article_type;
			return `<li>${who ? esc(who) + ": " : ""}${esc(f.error)}</li>`;
		}).join("");
		return items ? `<ul style="margin:6px 0 0">${items}</ul>` : "";
	}

	function showRates(d, data) {
		const quotes = data.quotes || [];
		if (!quotes.length) return showMessage(d, `${__("No rates returned.")}${failureList(data.failures)}`, "warning");
		const rows = quotes.map((q) => `
			<tr>
				<td>${esc(q.label || q.article_type)}</td>
				<td class="text-right">${q.chargeable_weight ?? ""} g</td>
				<td class="text-right"><b>${format_currency(q.final_amount, "INR")}</b></td>
			</tr>`).join("");
		const missing = (data.failures || []).length
			? `<div class="text-muted small">${__("Not available:")}${failureList(data.failures)}</div>` : "";
		d.fields_dict.result.$wrapper.html(`
			<table class="table table-bordered table-sm" style="margin-top:8px">
				<thead><tr><th>${__("Product")}</th><th class="text-right">${__("Charged weight")}</th>
				<th class="text-right">${__("Total incl. GST")}</th></tr></thead>
				<tbody>${rows}</tbody>
			</table>${missing}`);
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
					  default: rc.address_line1,
					  description: rc.address_name ? __("From address {0}", [rc.address_name]) : __("No address on this document — type it in") },
					{ fieldname: "address_line2", fieldtype: "Data", label: __("Address line 2"), default: rc.address_line2 },
					{ fieldname: "address_line3", fieldtype: "Data", label: __("Address line 3"), default: rc.address_line3 },

					{ fieldtype: "Section Break", label: __("Parcel") },
					{ fieldname: "product", fieldtype: "Select", label: __("Product"), reqd: 1,
					  options: PRODUCTS, default: PRODUCTS.some((p) => p.value === last.product) ? last.product : "SP",
					  description: __("Light but thick parcel (≤ 500 g, over 2 cm)? Use Business Parcel.") },
					{ fieldname: "weight", fieldtype: "Int", label: __("Weight (grams)"), reqd: 1, default: last.weight,
					  description: __("Weigh the packed parcel. Whole grams.") },
					{ fieldtype: "Column Break" },
					{ fieldname: "length", fieldtype: "Float", label: __("Length (cm)"), reqd: 1, default: last.length || 30 },
					{ fieldname: "breadth", fieldtype: "Float", label: __("Breadth (cm)"), reqd: 1, default: last.breadth || 21 },
					{ fieldname: "height", fieldtype: "Float", label: __("Height (cm)"), reqd: 1, default: last.height || 5 },
					{ fieldtype: "Column Break" },
					{ fieldname: "cod", fieldtype: "Check", label: __("Cash on delivery"), default: 0,
					  depends_on: "eval:doc.product=='BUSINESS_PARCEL'", description: __("Business Parcel only") },
					{ fieldname: "cod_value", fieldtype: "Currency", label: __("COD amount"), depends_on: "eval:doc.cod && doc.product=='BUSINESS_PARCEL'",
					  default: def.outstanding || def.order_amount },
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
					if (parcel.article_type !== "BUSINESS_PARCEL") parcel.cod_value = 0;
					saveParcel({ product: parcel.article_type, weight: parcel.weight, length: parcel.length, breadth: parcel.breadth, height: parcel.height });
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
							const list = (out.errors || []).map((e) => `<li>${esc(e)}</li>`).join("");
							const head = out.stage === "validation"
								? __("Please fix these before booking:")
								: __("India Post rejected the booking:");
							return showMessage(d, `<b>${head}</b><ul style="margin:6px 0 0">${list}</ul>`, "danger");
						}
						d.hide();
						const s = out.shipment || {};
						frappe.show_alert({ message: __("Booked — AWB {0}, ₹{1}", [s.awb, s.charge]), indicator: "green" }, 7);
						if (out.label_url) window.open(out.label_url, "_blank");
						else if (out.label_error) frappe.msgprint(__("Booked, but the label could not be fetched yet: {0}. Use India Post → Print Label.", [out.label_error]));
						if ((out.warnings || []).length) {
							frappe.msgprint({ title: __("Note"), indicator: "orange", message: out.warnings.map(esc).join("<br>") });
						}
						frm.reload_doc();
					}).catch(() => d.get_primary_btn().prop("disabled", false));
				},
			});
			d.show();
		});
	}

	for (const doctype of ["Sales Invoice", "Delivery Note"]) {
		frappe.ui.form.on(doctype, { refresh });
	}
})();
