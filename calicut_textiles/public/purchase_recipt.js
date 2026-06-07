frappe.ui.form.on('Purchase Receipt', {
    onload: function(frm) {
        frm.barcode_scanner = new erpnext.utils.BarcodeScanner({
            frm: frm,
            scan_field_name: 'custom_type_barcode',
            items_table_name: 'items',
            barcode_field: 'barcode',
            serial_no_field: 'serial_no',
            batch_no_field: 'batch_no',
            uom_field: 'uom',
            qty_field: 'qty',
            prompt_qty: true,
            scan_api: "erpnext.stock.utils.scan_barcode"
        });
    },
    custom_type_barcode: function(frm) {
        frm.barcode_scanner.process_scan().catch(() => {
            frappe.msgprint(__('Unable to process barcode'));
        });
    },
    before_save: function(frm) {
        frm.doc.items.forEach((doc) => {
            frappe.model.set_value(doc.doctype, doc.name, "custom_barcode_scan", doc.barcode);
        })
    },
    // Header "Apply" button — push the header selling/retail % to every item row
    custom_apply: function(frm) {
        if (!frm.doc.custom_selling_percentage_ && !frm.doc.custom_retail_percentage_) {
            frappe.msgprint(__('Please enter at least one percentage value before applying.'));
            return;
        }
        frm.doc.items.forEach(row => {
            if (frm.doc.custom_selling_percentage_ !== undefined) {
                frappe.model.set_value(row.doctype, row.name, 'custom_selling_percentage', frm.doc.custom_selling_percentage_);
            }
            if (frm.doc.custom_retail_percentage_ !== undefined) {
                frappe.model.set_value(row.doctype, row.name, 'custom_retail_percentage_', frm.doc.custom_retail_percentage_);
            }
        });
        frappe.msgprint(__('Percentage applied successfully.'));
        frm.refresh_field('items');
    },
    // Header "Clear" button — clear the header + per-row selling/retail % and rates
    custom_clear: function(frm) {
        frm.set_value("custom_selling_percentage_", "");
        frm.set_value("custom_retail_percentage_", "");
        frm.doc.items.forEach(row => {
            frappe.model.set_value(row.doctype, row.name, 'custom_selling_percentage', " ");
            frappe.model.set_value(row.doctype, row.name, 'custom_retail_percentage_', " ");
            frappe.model.set_value(row.doctype, row.name, 'custom_selling_rate', " ");
            frappe.model.set_value(row.doctype, row.name, 'custom_retail_rate', " ");
        });
        frappe.msgprint(__('Percentage Cleared'));
        frm.refresh_field('items');
    }
});

frappe.ui.form.on("Purchase Receipt Item", {
    // Net Qty = Qty * PCS  (enter Qty and PCS, Net Qty auto-fills)
    qty: function(frm, cdt, cdn) {
        update_net_qty(frm, cdt, cdn);
    },
    custom_pcs: function(frm, cdt, cdn) {
        update_net_qty(frm, cdt, cdn);
    },
    // Selling % -> Selling Rate  (e.g. rate 120 + 10% = 132)
    custom_selling_percentage: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (row.rate && row.custom_selling_percentage) {
            frappe.model.set_value(cdt, cdn, 'custom_selling_rate',
                row.rate + (row.rate * row.custom_selling_percentage / 100));
        }
    },
    // Retail % -> Retail Rate
    custom_retail_percentage_: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (row.rate && row.custom_retail_percentage_) {
            frappe.model.set_value(cdt, cdn, 'custom_retail_rate',
                row.rate + (row.rate * row.custom_retail_percentage_ / 100));
        }
    },
    // Selling Rate -> Selling %  (reverse: enter a rate, % back-fills)
    custom_selling_rate: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (row.rate && row.custom_selling_rate) {
            frappe.model.set_value(cdt, cdn, 'custom_selling_percentage',
                ((row.custom_selling_rate - row.rate) / row.rate) * 100);
        }
    },
    // Retail Rate -> Retail %
    custom_retail_rate: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (row.rate && row.custom_retail_rate) {
            frappe.model.set_value(cdt, cdn, 'custom_retail_percentage_',
                ((row.custom_retail_rate - row.rate) / row.rate) * 100);
        }
    }
});

function update_net_qty(frm, cdt, cdn) {
    let row = locals[cdt][cdn];
    if (row.qty && row.custom_pcs) {
        frappe.model.set_value(cdt, cdn, 'custom_net_qty', row.qty * row.custom_pcs);
    }
}
