frappe.ui.form.on('Purchase Order', {
    refresh: function(frm) {
        if (frm.doc.docstatus == 1 && frm.doc.status != "To Bill") {
            frm.add_custom_button(__('Supplier Packing Slip'), function() {
                frappe.call({
                    method: "calicut_textiles.calicut_textiles.events.purchase_order.make_supplier_packing_slip",
                    args: {
                        purchase_order: frm.doc.name
                    },
                    callback: function(r) {
                        if (r.message) {
                            frappe.show_alert({
                                message: "Supplier Packing Slip is Created",
                                indicator: 'green'
                            }, 5);
                            frappe.set_route('Form', 'Supplier Packing Slip', r.message);
                        }
                    }
                });
            }, __('Create'));
        }
    }
});

frappe.ui.form.on("Purchase Order Item", {
    // UI "Net Qty" (field: qty) = UI "Qty" (field: custom_net_qty) * PCS (field: custom_pcs)
    custom_net_qty: function(frm, cdt, cdn) {
        update_qty(frm, cdt, cdn);
    },
    custom_pcs: function(frm, cdt, cdn) {
        update_qty(frm, cdt, cdn);
    }
});

function update_qty(frm, cdt, cdn) {
    let row = locals[cdt][cdn];
    if (row.custom_net_qty && row.custom_pcs) {
        frappe.model.set_value(cdt, cdn, 'qty', row.custom_net_qty * row.custom_pcs);
    }
}
