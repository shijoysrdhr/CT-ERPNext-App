# Copyright (c) 2026, Calicut Textiles and contributors
# For license information, please see license.txt

"""Actions for the "Batch Repack Worklist" report.

Three complementary one-click actions, all back-dated to just before the sales
invoice (ERPNext validates batch stock chronologically, so the entry must post
before the invoice or submit still fails). Each consumes worklist rows tagged
with the matching `action`:

  * create_material_transfer  — action "Transfer": exact short batch has stock in
                                another warehouse → Material Transfer it in.
  * create_repack             — action "Repack": move stock from a FIFO source
                                batch into the over-drawn (short) batch, same wh.
  * create_material_receipt   — action "Material Receipt": item genuinely short →
                                receive the missing qty into the short batch.

Resilience: one Stock Entry is created per sales invoice, each inside its own
DB savepoint. If one invoice fails (e.g. a back-dated source went negative), it
is rolled back and reported in `skipped`; the others still go through. Every
document created here is flagged `custom_system_generated = 1`.

Return: {"created": [stock_entry_names], "skipped": [{"si", "reason"}]}.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, get_link_to_form, strip_html

EPS = 0.0005
LEAD_MINUTES = 1  # post the entry this many minutes before the sales invoice


# ---------------------------------------------------------------------------
# Public actions
# ---------------------------------------------------------------------------

@frappe.whitelist()
def create_material_transfer(rows, submit=0):
	return _run(rows, "Transfer", ("short_batch", "source_warehouse"), _build_transfer, submit)


@frappe.whitelist()
def create_repack(rows, submit=0):
	return _run(rows, "Repack", ("short_batch", "source_batch"), _build_repack, submit)


@frappe.whitelist()
def create_material_receipt(rows, submit=0):
	return _run(rows, "Material Receipt", ("short_batch",), _build_material_receipt, submit)


# ---------------------------------------------------------------------------
# Orchestration (resilient: one savepoint per invoice)
# ---------------------------------------------------------------------------

def _run(rows, action, needs, builder, submit):
	rows = json.loads(rows) if isinstance(rows, str) else rows
	submit = cint(submit)

	valid = [
		r for r in rows
		if r.get("action") == action and flt(r.get("qty")) > EPS and all(r.get(k) for k in needs)
	]
	if not valid:
		frappe.throw(_("No '{0}' rows to process.").format(action))

	created, skipped = [], []
	for idx, (si, si_rows) in enumerate(_group_by_si(valid).items()):
		sp = f"brw_{idx}"
		frappe.db.savepoint(sp)
		try:
			created.append(builder(si, si_rows, submit))
		except Exception as e:
			frappe.db.rollback(save_point=sp)   # undo just this invoice, keep the rest
			skipped.append({"si": si or "?", "reason": _reason(e)})
			frappe.clear_messages()             # don't surface the per-invoice error globally

	return {"created": created, "skipped": skipped}


def _reason(e):
	msg = " ".join(strip_html(str(e) or "").split())  # collapse newlines/tabs
	return (msg or e.__class__.__name__)[:300]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _build_transfer(si, rows, submit):
	se = _new_entry("Material Transfer", si, rows)
	se.remarks = _("Auto transfer for shortfall on Sales Invoice {0}").format(si) if si else None
	for r in rows:
		se.append("items", {
			"item_code": r["item_code"], "qty": flt(r["qty"]),
			"s_warehouse": r["source_warehouse"], "t_warehouse": r["warehouse"],
			"use_serial_batch_fields": 1, "batch_no": r["short_batch"],
		})
	return _save(se, submit)


def _build_repack(si, rows, submit):
	se = _new_entry("Repack", si, rows)
	se.remarks = _("Auto batch-repack for shortfall on Sales Invoice {0}").format(si) if si else None
	for r in rows:
		qty = flt(r["qty"])
		se.append("items", {
			"item_code": r["item_code"], "qty": qty, "s_warehouse": r["warehouse"],
			"use_serial_batch_fields": 1, "batch_no": r["source_batch"],
		})
		se.append("items", {
			"item_code": r["item_code"], "qty": qty, "t_warehouse": r["warehouse"],
			"use_serial_batch_fields": 1, "batch_no": r["short_batch"],
		})
	return _save(se, submit)


def _build_material_receipt(si, rows, submit):
	se = _new_entry("Material Receipt", si, rows)
	se.remarks = _("Auto material receipt for shortage on Sales Invoice {0}").format(si) if si else None
	for r in rows:
		se.append("items", {
			"item_code": r["item_code"], "qty": flt(r["qty"]), "t_warehouse": r["warehouse"],
			"use_serial_batch_fields": 1, "batch_no": r["short_batch"],
			"basic_rate": _receipt_rate(r["item_code"], r["warehouse"], r["short_batch"]),
		})
	return _save(se, submit)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _group_by_si(rows):
	out = {}
	for r in rows:
		out.setdefault(r.get("si"), []).append(r)
	return out


def _new_entry(purpose, si, rows):
	"""New Stock Entry of the given purpose, company from the rows' (target)
	warehouse, back-dated to just before the sales invoice, flagged system-generated."""
	companies = {frappe.db.get_value("Warehouse", r["warehouse"], "company") for r in rows}
	if len(companies) > 1:
		frappe.throw(_("Rows for invoice {0} span multiple companies.").format(si or "?"))

	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = purpose
	se.purpose = purpose
	se.company = companies.pop()
	se.custom_system_generated = 1

	if si:
		si_dt = frappe.db.get_value("Sales Invoice", si, ["posting_date", "posting_time"])
		if si_dt and si_dt[0] is not None:
			dt = add_to_date(get_datetime(f"{si_dt[0]} {si_dt[1]}"), minutes=-LEAD_MINUTES)
			se.set_posting_time = 1
			se.posting_date = dt.date()
			se.posting_time = dt.strftime("%H:%M:%S")
	return se


def _save(se, submit):
	se.insert()
	if submit:
		se.submit()
	return se.name


def _receipt_rate(item_code, warehouse, batch_no):
	"""Incoming valuation for a Material Receipt: the short batch's existing rate,
	else the item's latest valuation rate in the warehouse, else 0 (flagged in UI)."""
	rate = frappe.db.sql(
		"""
		SELECT sbe.incoming_rate
		FROM `tabSerial and Batch Entry` sbe
		JOIN `tabSerial and Batch Bundle` b ON b.name = sbe.parent
		WHERE b.is_cancelled = 0 AND b.docstatus = 1
			AND sbe.batch_no = %s AND sbe.qty > 0 AND sbe.incoming_rate > 0
		ORDER BY b.creation DESC LIMIT 1
		""",
		batch_no,
	)
	if rate and rate[0][0]:
		return flt(rate[0][0])

	rate = frappe.db.sql(
		"""
		SELECT valuation_rate FROM `tabStock Ledger Entry`
		WHERE item_code = %s AND warehouse = %s AND is_cancelled = 0 AND valuation_rate > 0
		ORDER BY posting_date DESC, posting_time DESC, creation DESC LIMIT 1
		""",
		(item_code, warehouse),
	)
	return flt(rate[0][0]) if rate and rate[0][0] else 0.0
