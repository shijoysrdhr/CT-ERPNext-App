frappe.ui.form.on("Purchase Invoice Item", {
    // NOTE: field labels are reversed vs field names ->
    //   UI "Qty" = custom_net_qty,  UI "PCS" = custom_pcs,  UI "Net Qty" = qty
    // User enters Qty (custom_net_qty) + PCS (custom_pcs); Net Qty (qty) = Qty * PCS
    custom_net_qty: function(frm, cdt, cdn) {
        update_qty(frm, cdt, cdn);
    },
    custom_pcs: function(frm, cdt, cdn) {
        update_qty(frm, cdt, cdn);
    }
});

function update_qty(frm, cdt, cdn) {
    // UI "Net Qty" (field: qty) = UI "Qty" (field: custom_net_qty) * PCS
    let row = locals[cdt][cdn];
    if (row.custom_net_qty && row.custom_pcs) {
        frappe.model.set_value(cdt, cdn, 'qty', row.custom_net_qty * row.custom_pcs);
    }
}
