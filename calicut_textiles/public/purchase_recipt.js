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
    }
});
