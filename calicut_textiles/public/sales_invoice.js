frappe.ui.form.on('Sales Invoice', {
    onload: function(frm) {
        frm.barcode_scanner = new erpnext.utils.BarcodeScanner({
            frm: frm,
            scan_field_name: 'custom_type_barcode',
            items_table_name: 'items',
            barcode_field: 'barcode',
            serial_no_field: 'serial_no',
            batch_no_field: 'batch_no',
            uom_field: 'uom',
            qty_field: 'custom_net_qty',
            prompt_qty: true,
            scan_api: "erpnext.stock.utils.scan_barcode"
        });

        frm.get_field('custom_references').grid.cannot_add_rows = true;
    },
    refresh: function(frm){
        // ERPNext re-seeds the POS payment grid from the POS Profile and can
        // leave a stale/duplicate Mode of Payment row on screen (a full reload
        // clears it). Clean the model + force a fresh re-render so the duplicate
        // stops showing. See dedupe_payment_modes() for the (conservative) rules.
        if (frm.doc.is_pos) {
            dedupe_payment_modes(frm);
            frm.refresh_field('payments');
        }
        if (frm.doc.customer) {
            frappe.call({
                method: 'calicut_textiles.calicut_textiles.events.sales_invoice.set_user_and_customer_and_branch',
                args: {
                    user: frappe.session.user
                },
                callback: function (r) {
                    if (r.message) {
                        if (r.message.user_series && r.message.user_series.length > 0) {
                            var namingSeries = Array.isArray(r.message.user_series) ? r.message.user_series[0] : r.message.user_series;
                            frm.set_value('naming_series', namingSeries);
                        }
                        if (r.message.default_tax) {
                            frm.set_value('taxes_and_charges', r.message.default_tax);
                        }
                        if (r.message.default_branch) {
                            frm.set_value('custom_branch', r.message.default_branch);
                        }
                        if (r.message.default_price) {
                            frm.set_value('selling_price_list', r.message.default_price);
                        }
                    }
                }
            });
        }
    },
    custom_type_barcode: function(frm) {
        frm.barcode_scanner.process_scan().catch(() => {
            frappe.msgprint(__('Unable to process barcode'));
        });
    },
    custom_sales_person: function (frm) {
        validate_employee_selection(frm);
    },
    custom_checked_by: function (frm) {
        validate_employee_selection(frm);
    },
    scan_barcode: function (frm) {
        var barcode = frm.doc.scan_barcode;
        var custom_references = true
        if(frm.doc.custom_references){
            for (var i = 0; i < frm.doc.custom_references.length; i++) {
                    if (frm.doc.custom_references[i].references === barcode) {
                        custom_references = false;
                    }
                }
        }
        if(custom_references){
            var custom_references = frm.doc.custom_references || [];
            frm.add_child('custom_references',{
                'references':barcode,
                'timestamp':frappe.datetime.now_datetime()
            })
            frm.refresh_field('custom_references');
        }
    },
    is_return: function(frm) {
        if (frm.doc.is_return && frm.doc.custom_branch) {
            let namingSeries = '';
            if (frm.doc.custom_branch === 'Counter RT') {
                namingSeries = 'RTRET.####';
            } else if (frm.doc.custom_branch === 'Counter CT') {
                namingSeries = 'CTRET.####';
            }
            if (namingSeries) {
                frm.set_value('naming_series', namingSeries);
            }
        }
    },
    custom_branch: function(frm) {
        if (frm.doc.is_return) {
            let namingSeries = '';
            if (frm.doc.custom_branch === 'Counter RT') {
                namingSeries = 'RTRET.####';
            } else if (frm.doc.custom_branch === 'Counter CT') {
                namingSeries = 'CTRET.####';
            }
            if (namingSeries) {
                frm.set_value('naming_series', namingSeries);
            }
        }
    }
});

frappe.ui.form.on('Sales Invoice Item', {
    item_code: function(frm, cdt, cdn) {
        get_total(frm, cdt, cdn)
    },
    custom_net_qty: function(frm, cdt, cdn) {
        get_net_qty(frm, cdt, cdn)
        get_total(frm, cdt, cdn)
    },
    custom_pcs: function(frm, cdt, cdn) {
        get_net_qty(frm, cdt, cdn)
        get_total(frm, cdt, cdn)
    },
    rate: function(frm, cdt, cdn) {
        get_total(frm, cdt, cdn)
    },
    amount: function(frm, cdt, cdn) {
        get_total(frm, cdt, cdn);
    },
});

function dedupe_payment_modes(frm) {
    // Collapse duplicate POS payment rows that ERPNext's re-seed can leave behind.
    // Rules (deliberately conservative so live POS entry is never disturbed):
    //   - same mode_of_payment appearing twice -> keep one, carry over any amount
    //   - blank-mode rows are left alone on a draft (cashier may be mid-entry),
    //     and dropped only on a submitted invoice where they are pure noise.
    if (!frm.doc.is_pos || !(frm.doc.payments && frm.doc.payments.length)) return;
    let seen = {};
    let kept = [];
    let changed = false;
    frm.doc.payments.forEach(function (row) {
        let mode = row.mode_of_payment;
        if (!mode) {
            if (frm.doc.docstatus === 1 && !flt(row.amount)) { changed = true; return; }
            kept.push(row);
            return;
        }
        if (mode in seen) {
            let keep = seen[mode];
            if (flt(row.amount) && !flt(keep.amount)) {
                keep.amount = row.amount;
                keep.base_amount = row.base_amount;
            }
            changed = true;
            return;
        }
        seen[mode] = row;
        kept.push(row);
    });
    if (changed) {
        kept.forEach((r, i) => (r.idx = i + 1));
        frm.doc.payments = kept;
    }
}

function validate_employee_selection(frm) {
    if (frm.doc.custom_sales_person && frm.doc.custom_checked_by && frm.doc.custom_sales_person === frm.doc.custom_checked_by) {
        frappe.msgprint({
            title: __('Validation Error'),
            message: __('The Sales Person and Checked By fields cannot have the same employee.'),
            indicator: 'red'
        });
        frm.set_value('custom_checked_by', '');
    }
}

function get_net_qty(frm, cdt, cdn) {
    let row = locals[cdt][cdn];
    let qty = 0;

    qty = row.custom_net_qty * row.custom_pcs

    frappe.model.set_value(cdt, cdn, "qty", qty);
}

function get_total(frm, cdt, cdn) {
    let row = locals[cdt][cdn];
    if (row.item_tax_template) {
        frappe.call({
            method: "frappe.client.get",
            args: {
                doctype: "Item Tax Template",
                name: row.item_tax_template,
            },
            callback: function (r) {
                if (r.message) {
                    let tax_template = r.message;
                    let gst_rate = tax_template.gst_rate || 0;
                    let total = 0;
                    let rate = 0

                    rate = row.rate *  row.qty
                    console.log("rate",rate)

                    total = rate + (rate * gst_rate / 100);
                    console.log("total",total)

                    frappe.model.set_value(cdt, cdn, "custom_total", total);
                } else {
                    frappe.model.set_value(cdt, cdn, "custom_total", total);
                }
            },
        });
    }
}
