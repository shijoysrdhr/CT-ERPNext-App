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
  * proposes how to fix each, in a 4-tier cascade (most-truthful first):

      1. TRANSFER          - the exact short batch has stock in ANOTHER
                             warehouse (same company) -> Material Transfer it in.
      2. REPACK            - other batches of the same item are in the SAME
                             warehouse -> move stock between batches.
      3. TRANSFER + REPACK - a DIFFERENT batch of the item sits in a DIFFERENT
                             warehouse -> one cross-warehouse Repack consumes it
                             there and produces the short batch here.
      4. MATERIAL RECEIPT  - none anywhere -> receive the missing qty.

A single shortfall may split across tiers (e.g. transfer 5 + repack 11). The
report emits one row per allocation with an `action` so the matching button on
the .js can create the right document. All fixes are back-dated before the
invoice by calicut_textiles.api.batch_repack.

Everything here is judged **as of the moment the fix will post**, never as of
now. Because the fix is back-dated, a source batch can only lend what it held
then AND kept holding since — see lendable(). Ignoring that produced sources
that looked available but failed on submit with BatchNegativeStockError: batch
100038125 showed 0.2 on 28-Aug but held nothing on 24-Aug, its stock having
arrived on a sales return in between.
"""

import bisect

import frappe
from frappe import _
from frappe.utils import add_to_date, flt, get_datetime, getdate

from calicut_textiles.api.batch_repack import LEAD_MINUTES

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
	ledger = get_batch_ledger(items)                # {(batch, wh): (times, suffix_min, final)}
	reserved = get_reserved_by_batch(items)         # {(batch, wh): qty claimed by DRAFT invoices}
	taken = {}                                      # {(batch, wh): qty this run has already allocated}

	def free(key, at):
		"""How much (batch, warehouse) can really lend for an entry posted at `at`.

		Not its balance today, and not even its balance at `at`: removing qty at
		`at` drags every LATER balance down by the same qty, so the ceiling is the
		MINIMUM running balance from `at` onwards (see lendable()). A batch whose
		stock only arrived after `at` — a sales return, say — shows a balance now
		but can lend nothing then. Less what other drafts claim, and less what
		earlier rows of this same run already spoke for, so nothing is promised twice.
		"""
		return flt(lendable(ledger, key, at) - reserved.get(key, 0) - taken.get(key, 0), PREC)

	def spend(key, qty):
		taken[key] = flt(taken.get(key, 0) + qty, PREC)

	# Only batches still holding stock today can lend: today's balance is part of
	# every suffix, so a batch that has since gone to zero has a lendable of zero
	# at any earlier time too.
	in_stock = [k for k, (_t, _s, final) in ledger.items() if final > EPS]
	batch_item, batch_age = get_batch_meta(items)   # {batch: item}, {batch: age}
	item_names = get_item_names(items)
	wh_company = get_wh_company({wh for (_b, wh) in in_stock} | {d.warehouse for d in demand})

	# Repack index: same-warehouse other batches of an item, oldest first.
	repack_index = {}
	# Cross index: every (batch, warehouse) holding an item, oldest batch first —
	# the pool tier 3 draws from once same-warehouse stock is exhausted.
	cross_index = {}
	for (batch, wh) in in_stock:
		it = batch_item.get(batch)
		if it:
			repack_index.setdefault((it, wh), []).append(batch)
			cross_index.setdefault(it, []).append((batch, wh))
	for key in repack_index:
		repack_index[key].sort(key=lambda b: (batch_age.get(b) is None, batch_age.get(b) or FAR, b))
	for key in cross_index:
		cross_index[key].sort(
			key=lambda bw: (batch_age.get(bw[0]) is None, batch_age.get(bw[0]) or FAR, bw[0])
		)

	# The invoice draws at its own posting time; the fix is posted LEAD_MINUTES
	# before that. Both are judged as of then, not as of now.
	for d in demand:
		d.posted_at = get_datetime(f"{d.posting_date} {d.posting_time}")
	# Two drafts on one short batch are rivals for the same units, so serve them
	# oldest first out of a shared pool. Sized independently, each would count the
	# whole balance as its own and neither would look short — then the first to
	# submit takes it and the second fails.
	demand.sort(key=lambda d: (d.posted_at, d.si))
	claimed = {}                                    # {(short batch, wh): units spoken for}

	rows = []
	for d in demand:
		sw, item, sb = d.warehouse, d.item_code, d.batch_no
		t_fix = add_to_date(d.posted_at, minutes=-LEAD_MINUTES)

		bal = flt(max(0.0, lendable(ledger, (sb, sw), d.posted_at) - claimed.get((sb, sw), 0)), PREC)
		covered = flt(min(d.demand, bal), PREC)
		claimed[(sb, sw)] = flt(claimed.get((sb, sw), 0) + covered, PREC)
		shortfall = flt(d.demand - covered, PREC)
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
			[(wh, av) for (b, wh) in in_stock
				if b == sb and wh != sw and wh_company.get(wh) == comp
				for av in [free((sb, wh), t_fix)] if av > EPS],
			key=lambda x: -x[1],
		)
		for wh, _av in transfer_srcs:
			if remaining <= EPS:
				break
			cur = free((sb, wh), t_fix)
			take = flt(min(cur, remaining), PREC)
			if take <= EPS:
				continue
			rows.append({**base, "action": "Transfer", "source_warehouse": wh, "source_batch": sb,
				"source_avail": flt(cur, PREC), "qty": take,
				"status": _("Transfer from {0}").format(wh)})
			spend((sb, wh), take)
			remaining = flt(remaining - take, PREC)

		# Tier 2 — REPACK from other batches in the same warehouse (FIFO).
		for b in repack_index.get((item, sw), []):
			if remaining <= EPS:
				break
			if b == sb:
				continue
			cur = free((b, sw), t_fix)
			if cur <= EPS:
				continue
			take = flt(min(cur, remaining), PREC)
			if take <= EPS:
				continue
			rows.append({**base, "action": "Repack", "source_warehouse": sw, "source_batch": b,
				"source_avail": flt(cur, PREC), "qty": take,
				"status": _("Repack ready")})
			spend((b, sw), take)
			remaining = flt(remaining - take, PREC)

		# Tier 3 — TRANSFER + REPACK: a different batch of the item in a different
		# warehouse (FIFO). Tier 1 only moves the identical batch and tier 2 only
		# swaps batches in place, so without this the commonest real case —
		# "the goods are in Stores, just under another batch" — fell through to a
		# Material Receipt and invented stock that already existed.
		for b, wh in cross_index.get(item, []):
			if remaining <= EPS:
				break
			if wh == sw or b == sb:          # covered by tier 2 / tier 1 respectively
				continue
			if wh_company.get(wh) != comp:
				continue
			cur = free((b, wh), t_fix)
			if cur <= EPS:
				continue
			take = flt(min(cur, remaining), PREC)
			if take <= EPS:
				continue
			rows.append({**base, "action": "Transfer + Repack", "source_warehouse": wh, "source_batch": b,
				"source_avail": flt(cur, PREC), "qty": take,
				"status": _("Repack from {0} into {1}").format(wh, sw)})
			spend((b, wh), take)
			remaining = flt(remaining - take, PREC)

		# Tier 4 — MATERIAL RECEIPT for the true remainder (no stock anywhere).
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
			si.posting_date, si.posting_time,
			ROUND(SUM(sii.qty), {PREC}) AS demand
		FROM `tabSales Invoice Item` sii
		JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE {conditions}
			AND sii.batch_no IS NOT NULL AND sii.batch_no <> ''
		GROUP BY sii.parent, sii.item_code, sii.warehouse, sii.batch_no,
			si.posting_date, si.posting_time
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


def get_batch_ledger(items):
	"""Per (batch, warehouse), everything needed to answer "how much could this
	batch lend at time T" — for any T, not just now.

	Returns {(batch, wh): (times, suffix_min, final)} where `times` are the
	movement timestamps in order, `suffix_min[i]` is the lowest running balance
	from movement i onwards, and `final` is today's balance.

	Batch quantities live in Serial and Batch Bundle in v15 (SLE.batch_no is
	always empty here — checked: zero rows carry it), but the bundle itself has no
	reliable posting date, so the timeline comes from the Stock Ledger Entry the
	bundle is attached to.
	"""
	rows = frappe.db.sql(
		"""
		SELECT sbe.batch_no, sbe.warehouse, sbe.qty,
			TIMESTAMP(sle.posting_date, sle.posting_time) AS posted_at
		FROM `tabStock Ledger Entry` sle
		JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sle.serial_and_batch_bundle
		JOIN `tabBatch` b ON b.name = sbe.batch_no
		WHERE sle.is_cancelled = 0 AND b.item IN %(items)s
		ORDER BY sle.posting_date, sle.posting_time, sle.creation
		""",
		{"items": items},
		as_dict=True,
	)

	moves = {}
	for r in rows:
		moves.setdefault((r.batch_no, r.warehouse), []).append((r.posted_at, flt(r.qty, PREC)))

	ledger = {}
	for key, ms in moves.items():
		times, running, bal = [], [], 0.0
		for posted_at, qty in ms:
			bal = flt(bal + qty, PREC)
			times.append(posted_at)
			running.append(bal)
		suffix_min, lowest = [0.0] * len(running), None
		for i in range(len(running) - 1, -1, -1):
			lowest = running[i] if lowest is None else min(lowest, running[i])
			suffix_min[i] = lowest
		ledger[key] = (times, suffix_min, running[-1] if running else 0.0)
	return ledger


def lendable(ledger, key, at):
	"""Most that can leave (batch, warehouse) at `at` without pushing the batch
	negative at that moment or at any moment after it.

	Taking qty out at `at` shifts every subsequent balance down by that qty, so the
	ceiling is the minimum running balance over [at, now] — not the balance at
	`at`, and certainly not the balance today. Zero if the batch had no movement
	before `at` (it did not exist yet) or if it ever dips to nothing afterwards.
	"""
	entry = ledger.get(key)
	if not entry:
		return 0.0
	times, suffix_min, _final = entry
	i = bisect.bisect_right(times, at) - 1
	if i < 0:
		return 0.0
	return max(0.0, flt(suffix_min[i], PREC))


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
