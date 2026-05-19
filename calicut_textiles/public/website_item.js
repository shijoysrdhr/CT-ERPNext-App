frappe.ui.form.on("Website Item", {
	refresh(frm) {
		// On every form load, refresh the stored batch qty so the user sees live data
		// without having to wait for SLE events to propagate.
		if (frm.doc.custom_batch_no && !frm.is_new()) {
			refresh_batch_qty(frm);
		}
	},

	custom_batch_no(frm) {
		if (!frm.doc.custom_batch_no) {
			return;
		}
		frappe.call({
			method: "calicut_textiles.api.webshop.get_batch_details",
			args: { batch_no: frm.doc.custom_batch_no },
			callback(r) {
				if (!r.message) return;
				const data = r.message;

				// item_code auto-fetches via Fetch From, but set it explicitly as a safety net
				if (data.item_code) {
					frm.set_value("item_code", data.item_code);
				}

				// Seed Website Item Name with the batch number — user can rename it anytime
				if (!frm.doc.web_item_name) {
					frm.set_value("web_item_name", frm.doc.custom_batch_no);
				}

				// Route slug: products/<batch_no>
				frm.set_value("route", `products/${frm.doc.custom_batch_no}`);

				// Stock warehouse — where this batch actually has units
				if (data.warehouse) {
					frm.set_value("website_warehouse", data.warehouse);
				}

				// Webshop price from Item Price (batch-specific, else item-level)
				frm.set_value("custom_webshop_price", data.price || 0);

				// Current batch qty into the stored field — shows up in form + list view
				frm.set_value("custom_current_batch_qty", data.qty || 0);
			},
		});
	},
});

function refresh_batch_qty(frm) {
	frappe.call({
		method: "calicut_textiles.api.webshop.get_batch_details",
		args: { batch_no: frm.doc.custom_batch_no },
		callback(r) {
			if (r.message && r.message.qty !== frm.doc.custom_current_batch_qty) {
				// Update silently — don't trigger a dirty form state
				frm.doc.custom_current_batch_qty = r.message.qty || 0;
				frm.refresh_field("custom_current_batch_qty");
			}
		},
	});
}
