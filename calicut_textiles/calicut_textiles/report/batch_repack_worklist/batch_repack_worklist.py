# Copyright (c) 2026, Calicut Textiles and contributors
# For license information, please see license.txt

"""Batch Repack Worklist
=========================
Every morning the user submits draft Sales Invoices and ERPNext blocks the ones
whose batch goes negative (sales staff bump a scanned row's qty instead of
adding a second batch row). This report does that hunt *before* submitting:

  * scans every DRAFT Sales Invoice line that carries a batch_no,
  * projects each (batch, warehouse) balance after the draft demand,
  * lists every batch that will go negative (the shortfall), and
  * proposes how to fix each, in a 3-tier cascade (most-truthful first):

      1. TRANSFER          - the exact short batch has stock in ANOTHER
                             warehouse (same company) -> Material Transfer it in.
      2. REPACK            - other batches of the same item are in the SAME
                             warehouse -> move stock between batches.
      3. MATERIAL RECEIPT  - none anywhere -> receive the missing qty.

A single shortfall may split across tiers (e.g. transfer 5 + repack 11). The
report emits one row per allocation with an `action` so the matching button on
the .js can create the right document. All fixes are back-dated before the
invoice by calicut_textiles.api.batch_repack.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

PREC = 3
EPS = 0.0005
FAR = getdate("2999-12-31")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Draft Invoice"), "fieldname": "si", "fieldtype": "Link", "options": "Sales Invoice", "width": 120},
		{"label": _("Commented"), "fieldname": "commented", "fieldtype": "Data", "width": 95},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 100},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 150},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 110},
		{"label": _("Short Batch"), "fieldname": "short_batch", "fieldtype": "Link", "options": "Batch", "width": 110},
		{"label": _("Balance"), "fieldname": "balance", "fieldtype": "Float", "width": 80, "precision": PREC},
		{"label": _("Demand"), "fieldname": "demand", "fieldtype": "Float", "width": 80, "precision": PREC},
		{"label": _("Shortfall"), "fieldname": "shortfall", "fieldtype": "Float", "width": 80, "precision": PREC},
		{"label": _("Action"), "fieldname": "action", "fieldtype": "Data", "width": 120},
		{"label": _("Source Warehouse"), "fieldname": "source_warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 130},
		{"label": _("Source Batch"), "fieldname": "source_batch", "fieldtype": "Link", "options": "Batch", "width": 110},
		{"label": _("Source Avail"), "fieldname": "source_avail", "fieldtype": "Float", "width": 95, "precision": PREC},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 80, "precision": PREC},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 170},
	]


def get_data(filters):
	demand = get_draft_demand(filters)
	if not demand:
		return []

	commented = get_commented_sis({d.si for d in demand})
	if filters.get("only_commented"):
		demand = [d for d in demand if d.si in commented]
		if not demand:
			return []
	items = list({d.item_code for d in demand})
	balances = get_balances(items)                  # {(batch, wh): balance} all warehouses
	reserved = get_reserved_by_batch(items)         # {(batch, wh): qty claimed by DRAFT invoices}
	# Free stock = balance minus what draft invoices already claim on that batch.
	# Without this, a batch being sold on the very invoice we're fixing (or on
	# another draft) looks "available" and gets robbed as a repack/transfer source,
	# driving it negative on submit. Reserve is company-wide across all drafts, not
	# just the filtered invoice, so concurrent drafts can't double-claim one batch.
	avail = {}                                      # mutable shared pool of FREE stock
	for k, bal in balances.items():
		free = flt(bal - reserved.get(k, 0), PREC)
		if free > EPS:
			avail[k] = free
	batch_item, batch_age = get_batch_meta(items)   # {batch: item}, {batch: age}
	item_names = get_item_names(items)
	wh_company = get_wh_company({wh for (_b, wh) in avail} | {d.warehouse for d in demand})

	# Repack index: same-warehouse other batches of an item, oldest first.
	repack_index = {}
	for (batch, wh) in avail:
		it = batch_item.get(batch)
		if it:
			repack_index.setdefault((it, wh), []).append(batch)
	for key in repack_index:
		repack_index[key].sort(key=lambda b: (batch_age.get(b) is None, batch_age.get(b) or FAR, b))

	rows = []
	for d in demand:
		sw, item, sb = d.warehouse, d.item_code, d.batch_no
		bal = flt(balances.get((sb, sw), 0), PREC)
		shortfall = flt(d.demand - bal, PREC)
		if shortfall <= EPS:
			continue

		remaining = shortfall
		comp = wh_company.get(sw)
		base = {
			"si": d.si, "commented": "Yes" if d.si in commented else "No",
			"item_code": item, "item_name": item_names.get(item),
			"warehouse": sw, "short_batch": sb, "balance": bal,
			"demand": d.demand, "shortfall": shortfall,
		}

		# Tier 1 — TRANSFER the exact short batch from other warehouses (most available first).
		transfer_srcs = sorted(
			[(wh, av) for (b, wh), av in avail.items()
				if b == sb and wh != sw and av > EPS and wh_company.get(wh) == comp],
			key=lambda x: -x[1],
		)
		for wh, _av in transfer_srcs:
			if remaining <= EPS:
				break
			cur = avail.get((sb, wh), 0)
			take = flt(min(cur, remaining), PREC)
			if take <= EPS:
				continue
			rows.append({**base, "action": "Transfer", "source_warehouse": wh, "source_batch": sb,
				"source_avail": flt(cur, PREC), "qty": take,
				"status": _("Transfer from {0}").format(wh)})
			avail[(sb, wh)] = flt(cur - take, PREC)
			remaining = flt(remaining - take, PREC)

		# Tier 2 — REPACK from other batches in the same warehouse (FIFO).
		for b in repack_index.get((item, sw), []):
			if remaining <= EPS:
				break
			if b == sb:
				continue
			cur = avail.get((b, sw), 0)
			if cur <= EPS:
				continue
			take = flt(min(cur, remaining), PREC)
			if take <= EPS:
				continue
			rows.append({**base, "action": "Repack", "source_warehouse": sw, "source_batch": b,
				"source_avail": flt(cur, PREC), "qty": take,
				"status": _("Repack ready")})
			avail[(b, sw)] = flt(cur - take, PREC)
			remaining = flt(remaining - take, PREC)

		# Tier 3 — MATERIAL RECEIPT for the true remainder (no stock anywhere).
		if remaining > EPS:
			rows.append({**base, "action": "Material Receipt", "source_warehouse": None, "source_batch": None,
				"source_avail": 0, "qty": flt(remaining, PREC),
				"status": _("⚠ No stock anywhere — receive {0}").format(flt(remaining, PREC))})

	return rows


def get_draft_demand(filters):
	conditions = "si.docstatus = 0"
	params = {}
	if filters.get("company"):
		conditions += " AND si.company = %(company)s"
		params["company"] = filters.company
	if filters.get("sales_invoice"):
		conditions += " AND si.name = %(sales_invoice)s"
		params["sales_invoice"] = filters.sales_invoice

	return frappe.db.sql(
		f"""
		SELECT sii.parent AS si, sii.item_code, sii.warehouse, sii.batch_no,
			ROUND(SUM(sii.qty), {PREC}) AS demand
		FROM `tabSales Invoice Item` sii
		JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE {conditions}
			AND sii.batch_no IS NOT NULL AND sii.batch_no <> ''
		GROUP BY sii.parent, sii.item_code, sii.warehouse, sii.batch_no
		""",
		params,
		as_dict=True,
	)


def get_reserved_by_batch(items):
	"""Qty already committed to DRAFT Sales Invoices per (batch, warehouse), across
	ALL draft invoices company-wide (deliberately NOT filtered to the report's
	sales_invoice). Subtracted from balance to get the FREE stock a batch can lend
	as a repack/transfer source — so a batch that is itself being sold on a draft
	can never be chosen as a source and driven negative on submit."""
	rows = frappe.db.sql(
		f"""
		SELECT sii.batch_no, sii.warehouse, ROUND(SUM(sii.qty), {PREC}) AS reserved
		FROM `tabSales Invoice Item` sii
		JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE si.docstatus = 0
			AND sii.batch_no IS NOT NULL AND sii.batch_no <> ''
			AND sii.item_code IN %(items)s
		GROUP BY sii.batch_no, sii.warehouse
		""",
		{"items": items},
		as_dict=True,
	)
	return {(r.batch_no, r.warehouse): flt(r.reserved, PREC) for r in rows}


def get_commented_sis(sis):
	"""Sales Invoices carrying at least one user comment (comment_type 'Comment').
	Cashiers leave a note when a shortage blocks submit, e.g. 'completed sale —
	check stock and submit', so this flags which shortfalls to action first."""
	sis = [s for s in sis if s]
	if not sis:
		return set()
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT reference_name FROM `tabComment`
		WHERE reference_doctype = 'Sales Invoice' AND comment_type = 'Comment'
			AND reference_name IN %(sis)s
		""",
		{"sis": sis},
	)
	return {r[0] for r in rows}


def get_balances(items):
	"""Live batch balance per (batch, warehouse), all warehouses, scoped to the
	demand items. Verified identical to erpnext ...batch.get_batch_qty."""
	rows = frappe.db.sql(
		f"""
		SELECT sbe.batch_no, sbe.warehouse, ROUND(SUM(sbe.qty), {PREC}) AS balance
		FROM `tabSerial and Batch Entry` sbe
		JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
		JOIN `tabBatch` b ON b.name = sbe.batch_no
		WHERE sbb.is_cancelled = 0 AND sbb.docstatus = 1
			AND b.item IN %(items)s
		GROUP BY sbe.batch_no, sbe.warehouse
		""",
		{"items": items},
		as_dict=True,
	)
	return {(r.batch_no, r.warehouse): flt(r.balance, PREC) for r in rows}


def get_batch_meta(items):
	meta = frappe.db.sql(
		"""
		SELECT name AS batch, item, COALESCE(manufacturing_date, DATE(creation)) AS age
		FROM `tabBatch`
		WHERE item IN %(items)s AND disabled = 0
		""",
		{"items": items},
		as_dict=True,
	)
	return {m.batch: m.item for m in meta}, {m.batch: m.age for m in meta}


def get_item_names(items):
	rows = frappe.get_all("Item", filters={"name": ["in", items]}, fields=["name", "item_name"])
	return {r.name: r.item_name for r in rows}


def get_wh_company(warehouses):
	warehouses = [w for w in warehouses if w]
	if not warehouses:
		return {}
	rows = frappe.get_all("Warehouse", filters={"name": ["in", warehouses]}, fields=["name", "company"])
	return {r.name: r.company for r in rows}
