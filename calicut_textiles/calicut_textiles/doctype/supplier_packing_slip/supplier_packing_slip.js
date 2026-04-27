// Copyright (c) 2024, sammish and contributors
// For license information, please see license.txt

frappe.ui.form.on("Supplier Packing Slip", {
    setup : function(frm) {
        frm.ignore_doctypes_on_cancel_all = ["Serial and Batch Bundle"];
    },
    onload(frm){
        frm.get_field('supplier_packing_slip_item').grid.cannot_add_rows = true;

        // Tab from PCS input behaves like clicking Add: split the row when
        // PO remaining qty is still positive so the user can keep entering
        // packings for the same item without leaving the keyboard.
        // Bound at document level in capture phase + stopImmediatePropagation
        // so we always run BEFORE Frappe's own grid Tab handler.
        if (!document._sps_pcs_tab_bound) {
            document._sps_pcs_tab_bound = true;
            document.addEventListener('keydown', function (e) {
                if (e.key !== 'Tab' || e.shiftKey) return;
                const target = e.target;
                if (!target || target.tagName !== 'INPUT') return;
                const $target = $(target);
                const $cell = $target.closest('[data-fieldname]');
                if ($cell.attr('data-fieldname') !== 'pcs') return;
                const $row = $target.closest('.grid-row');
                const cdn = $row.attr('data-name');
                const $parentTable = $target.closest('[data-fieldname="supplier_packing_slip_item"]');
                if (!cdn || !$parentTable.length) return;

                // Resolve the right form (document-level listener may outlive a single form).
                const active_frm = window.cur_frm && window.cur_frm.doc && window.cur_frm.doc.doctype === 'Supplier Packing Slip' ? window.cur_frm : null;
                if (!active_frm) return;

                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                // Commit PCS value -> fires the `pcs` change handler which
                // recomputes `qty` and (after 100ms) po_remaining_qty.
                $target.blur();
                setTimeout(
                    () => add_packing_row(active_frm, 'Supplier Packing Slip Item', cdn),
                    200,
                );
            }, true);
        }
    },
    validate(frm) {
        // Consolidate duplicate rows (same item + per-piece qty + lot + PO line)
        // BEFORE the save request leaves the browser. Doing it here keeps the
        // grid, frm.doc, and the request payload in sync — running it only on
        // the server leaves the client grid with stale rows post-save.
        // Server-side validate still runs the same logic as a safety net.
        consolidate_packing_items_client(frm);
    },
	refresh(frm) {
        if (frm.doc.docstatus == 1 && frm.doc.purchase_receipt != 1) {
            frm.add_custom_button(__('Purchase Receipt'), function() {
                frappe.call({
                    method: "calicut_textiles.calicut_textiles.doctype.supplier_packing_slip.supplier_packing_slip.make_purchase_receipt",
                    args: {
                        packing_slip: frm.doc.name
                    },
                    callback: function(r) {
                        if (r.message) {
                            frappe.show_alert({
                                message: "Purchase Receipt is Created",
                                indicator: 'green'
                            }, 5);
                            frappe.set_route('Form', 'Purchase Receipt', r.message);
                        }
                    }
                });
            }, __('Create'));
        }

	},
});
frappe.ui.form.on('Supplier Packing Slip Item', {
    add: function(frm, cdt, cdn) {
        add_packing_row(frm, cdt, cdn);
    },

    qty: function(frm, cdt, cdn) {
        set_remaining_qty(frm, cdt, cdn);
    },

    pcs: function(frm, cdt, cdn) {
        update_net_qty(frm, cdt, cdn);
    },

    custom_qty: function(frm, cdt, cdn) {
        update_net_qty(frm, cdt, cdn);
    },
});

function consolidate_packing_items_client(frm) {
    const rows = (frm.doc.supplier_packing_slip_item || []).slice();
    if (!rows.length) return;

    // Capture each PO line's original po_actual_qty from its first occurrence.
    const original_po_qty = {};
    for (const item of rows) {
        if (item.purchase_order_item && original_po_qty[item.purchase_order_item] === undefined) {
            original_po_qty[item.purchase_order_item] = flt(item.po_actual_qty);
        }
    }

    const seen = {};
    const duplicates = [];
    for (const item of rows) {
        if (!item.item_code) {
            duplicates.push(item);
            continue;
        }
        const key = [
            item.item_code,
            flt(item.custom_qty),
            item.lot_no || '',
            item.purchase_order_item || '',
        ].join('|');
        const existing = seen[key];
        if (existing) {
            existing.pcs = flt(existing.pcs) + flt(item.pcs);
            existing.qty = flt(existing.custom_qty) * flt(existing.pcs);
            duplicates.push(item);
        } else {
            seen[key] = item;
        }
    }

    if (!duplicates.length) return;

    // Drop merged rows from Frappe's local model so the grid + payload stay in sync.
    for (const dup of duplicates) {
        frappe.model.clear_doc(dup.doctype, dup.name);
    }

    // Reindex + recalc PO running totals on whatever rows remain.
    const remaining = frm.doc.supplier_packing_slip_item || [];
    const used = {};
    remaining.forEach((item, idx) => {
        item.idx = idx + 1;
        const po_item = item.purchase_order_item;
        if (po_item && original_po_qty[po_item] !== undefined) {
            const used_so_far = used[po_item] || 0;
            item.po_actual_qty = original_po_qty[po_item] - used_so_far;
            item.po_remaining_qty = item.po_actual_qty - flt(item.qty);
            used[po_item] = used_so_far + flt(item.qty);
        }
    });

    frm.refresh_field('supplier_packing_slip_item');
}

function add_packing_row(frm, cdt, cdn) {
    let row = frappe.get_doc(cdt, cdn);

    if (flt(row.qty) <= 0) {
        frappe.msgprint(__('Quantity must be greater than zero.'));
        return;
    }

    if (flt(row.qty) > flt(row.po_actual_qty)) {
        frappe.msgprint(__('Quantity is more than Actual Qty.'));
        return;
    }

    let remaining_qty = flt(row.po_actual_qty) - flt(row.qty);
    frappe.model.set_value(cdt, cdn, 'po_remaining_qty', remaining_qty);

    let table = frm.doc.supplier_packing_slip_item;
    let current_index = table.findIndex(d => d.name === row.name);

    if (remaining_qty <= 0) {
        // PO qty for this item fully consumed — jump to the next row whose
        // qty is still zero (i.e. the next item the user hasn't filled yet).
        frm.refresh_field('supplier_packing_slip_item');
        for (let i = current_index + 1; i < table.length; i++) {
            if (flt(table[i].qty) === 0) {
                focus_cell_input(
                    frm,
                    'supplier_packing_slip_item',
                    table[i].name,
                    i,
                    'custom_qty',
                );
                break;
            }
        }
        return;
    }

    let new_row = frm.add_child('supplier_packing_slip_item');
    new_row.item_code = row.item_code;
    new_row.qty = 0;
    new_row.uom = row.uom;
    new_row.po_ref = row.po_ref;
    new_row.po_actual_qty = remaining_qty;
    new_row.po_remaining_qty = remaining_qty;
    new_row.purchase_order_item = row.purchase_order_item;
    new_row.lot_no = row.lot_no;

    table.splice(table.length - 1, 1);
    table.splice(current_index + 1, 0, new_row);
    table.forEach((d, i) => d.idx = i + 1);

    frm.refresh_field('supplier_packing_slip_item');

    // Continue keyboard flow: focus new row's Qty (custom_qty) input.
    focus_cell_input(frm, 'supplier_packing_slip_item', new_row.name, current_index + 1, 'custom_qty');
}

function focus_cell_input(frm, table_fieldname, row_name, row_index, cell_fieldname) {
    // After a grid refresh + add_child, the new row's input is rendered lazily.
    // Resolve the row by DOM (data-name attr) first, then fall back to
    // grid.grid_rows[index]. Poll for up to ~1s while the grid finishes laying out.
    const start = Date.now();
    let attempts = 0;
    const try_focus = () => {
        attempts++;
        const grid = frm.fields_dict[table_fieldname]?.grid;
        // Prefer DOM lookup — survives any grid_rows_by_docname mismatch.
        let $row = grid?.wrapper?.find(`.grid-row[data-name="${row_name}"]`);
        if (!$row || !$row.length) {
            // Fall back to index-based lookup
            const gr = grid?.grid_rows?.[row_index];
            if (gr?.$wrapper) $row = gr.$wrapper;
        }
        if (!$row || !$row.length) {
            if (Date.now() - start < 1000) setTimeout(try_focus, 50);
            return;
        }
        const $cell = $row.find(`[data-fieldname="${cell_fieldname}"]`).first();
        if (!$cell.length) {
            if (Date.now() - start < 1000) setTimeout(try_focus, 50);
            return;
        }
        const cellEl = $cell[0];
        ['mousedown', 'mouseup', 'click'].forEach((type) => {
            cellEl.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
        });
        const $input = $cell.find('input, textarea').first();
        if ($input.length) {
            $input[0].focus();
            try { $input[0].select(); } catch (_) {}
            return;
        }
        if (Date.now() - start < 1000) setTimeout(try_focus, 50);
    };
    setTimeout(try_focus, 150);
}

function set_remaining_qty(frm, cdt, cdn) {
    var child = locals[cdt][cdn];
    var po_remaining_qty = child.po_actual_qty - child.qty;
    frappe.model.set_value(child.doctype, child.name, 'po_remaining_qty', po_remaining_qty);
}

function update_net_qty(frm, cdt, cdn) {
    let current_row = locals[cdt][cdn];
    let new_qty = current_row.pcs * current_row.custom_qty;
    frappe.model.set_value(current_row.doctype, current_row.name, 'qty', new_qty);

    // Delay to ensure 'qty' value is updated before recalculating remaining quantities
    setTimeout(() => {
        update_remaining_quantities(frm);
    }, 100);
}

function update_remaining_quantities(frm) {
    let rows = frm.doc.supplier_packing_slip_item;
    let po_actual_qty_map = {};
    let used_qty_map = {};

    // Step 1: Prepare map of original po_actual_qty for each PO Item
    rows.forEach(row => {
        if (!po_actual_qty_map[row.purchase_order_item]) {
            po_actual_qty_map[row.purchase_order_item] = row.po_actual_qty;
        }
    });

    // Step 2: Recalculate and update po_remaining_qty for all rows
    rows.forEach((row, idx) => {
        let po_item = row.purchase_order_item;

        if (!used_qty_map[po_item]) {
            used_qty_map[po_item] = 0;
        }

        // Remaining qty before this row
        let remaining_qty = po_actual_qty_map[po_item] - used_qty_map[po_item];

        // Update this row's po_actual_qty (in case it needs correction)
        frappe.model.set_value(row.doctype, row.name, 'po_actual_qty', remaining_qty);

        // Calculate po_remaining_qty after using this row's qty
        let row_qty = row.qty || 0;
        let po_remaining_qty = remaining_qty - row_qty;
        frappe.model.set_value(row.doctype, row.name, 'po_remaining_qty', po_remaining_qty);

        // Update used qty for next rows
        used_qty_map[po_item] += row_qty;
    });
}
