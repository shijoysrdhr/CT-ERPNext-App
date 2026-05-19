frappe.ui.form.on("Website Item", {
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

				// Seed Display Name with the batch number — user can edit it to anything
				if (!frm.doc.custom_display_name) {
					frm.set_value("custom_display_name", frm.doc.custom_batch_no);
				}

				// Route slug: products/<batch_no>
				frm.set_value("route", `products/${frm.doc.custom_batch_no}`);

				// Stock warehouse — where this batch actually has units
				if (data.warehouse) {
					frm.set_value("website_warehouse", data.warehouse);
				}

				// Webshop price from Item Price (batch-specific, else item-level)
				frm.set_value("custom_webshop_price", data.price || 0);

				if (data.qty) {
					frappe.show_alert({
						message: __("Batch {0}: {1} units available in {2}", [
							frm.doc.custom_batch_no,
							data.qty,
							data.warehouse,
						]),
						indicator: "green",
					});
				} else {
					frappe.show_alert({
						message: __("Batch {0} has no positive stock — listing will show as out of stock", [
							frm.doc.custom_batch_no,
						]),
						indicator: "orange",
					});
				}
			},
		});
	},

	custom_display_name(frm) {
		// Keep the standard webshop title (web_item_name) in sync with what the user typed
		if (frm.doc.custom_display_name) {
			frm.set_value("web_item_name", frm.doc.custom_display_name);
		}
	},

	validate(frm) {
		// Safety net: if web_item_name is somehow out of sync at save time, force it
		if (frm.doc.custom_display_name && frm.doc.web_item_name !== frm.doc.custom_display_name) {
			frm.doc.web_item_name = frm.doc.custom_display_name;
		}
	},
});
