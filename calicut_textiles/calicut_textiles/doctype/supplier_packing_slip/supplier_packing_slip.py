# Copyright (c) 2024, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import flt


class SupplierPackingSlip(Document):
    def validate(self):
        self.consolidate_items()

    def on_submit(self):
        for item in self.supplier_packing_slip_item:
            if item.qty == 0:
                frappe.throw(_("Cannot submit Packing Slip with zero quantity item"))

    def on_cancel(self):
        if self.purchase_receipt:
            self.db_set('purchase_receipt', 0)

    def consolidate_items(self):
        """Merge rows that describe the same physical packing — same item, same
        per-piece qty, same lot, same PO line — by summing PCS into the first
        occurrence and dropping the duplicates. Per-row PO running totals are
        recomputed afterwards so po_actual_qty / po_remaining_qty stay correct.
        """
        rows = list(self.supplier_packing_slip_item or [])
        if not rows:
            return

        # Capture each PO line's original po_actual_qty from the FIRST row that
        # references it — that row holds the un-split baseline.
        original_po_qty = {}
        for item in rows:
            po_item = item.purchase_order_item
            if po_item and po_item not in original_po_qty:
                original_po_qty[po_item] = flt(item.po_actual_qty)

        seen = {}
        keepers = []
        duplicates = []
        for item in rows:
            if not item.item_code:
                # drop empty stub rows defensively
                duplicates.append(item)
                continue
            key = (
                item.item_code,
                flt(item.custom_qty),
                item.lot_no or "",
                item.purchase_order_item or "",
            )
            existing = seen.get(key)
            if existing is not None:
                existing.pcs = flt(existing.pcs) + flt(item.pcs)
                existing.qty = flt(existing.custom_qty) * flt(existing.pcs)
                duplicates.append(item)
            else:
                seen[key] = item
                keepers.append(item)

        if not duplicates:
            return

        for dup in duplicates:
            self.remove(dup)

        used = {}
        for idx, item in enumerate(keepers, 1):
            item.idx = idx
            po_item = item.purchase_order_item
            if po_item and po_item in original_po_qty:
                used_so_far = used.get(po_item, 0.0)
                item.po_actual_qty = original_po_qty[po_item] - used_so_far
                item.po_remaining_qty = item.po_actual_qty - flt(item.qty)
                used[po_item] = used_so_far + flt(item.qty)

@frappe.whitelist()
def make_purchase_receipt(packing_slip):

    packing = frappe.get_doc("Supplier Packing Slip", packing_slip)
    order = frappe.get_doc("Purchase Order", packing.purchase_order)
  
    
    pr = frappe.get_doc({
        'doctype': 'Purchase Receipt',
        'posting_date': packing.posting_date,
        'company': packing.company,
        'supplier': packing.supplier
    })


    for item in packing.supplier_packing_slip_item:
        po_item = frappe.get_doc("Purchase Order Item", item.purchase_order_item)
        pr_item = pr.append("items", {})
        pr_item.item_code = item.item_code
        pr_item.item_name = po_item.item_name
        pr_item.uom = item.uom
        pr_item.qty = item.qty
        pr_item.custom_pcs = item.pcs
        pr_item.custom_net_qty = item.custom_qty
        pr_item.item_group = po_item.item_group
        pr_item.rate = po_item.rate
        pr_item.purchase_order = item.po_ref
        pr_item.custom_supplier_packing_slip = item.parent
        pr_item.custom_supplier_packing_slip_item = item.name
        pr_item.purchase_order_item = item.purchase_order_item
        
    pr.taxes = order.taxes
    pr.insert(ignore_permissions=True)

    return pr.name