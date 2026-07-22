"""Backend for the Cash Approvals web app (/approvals).

Thin layer over Frappe's workflow engine - it never re-implements approval
rules. `get_transitions` / `apply_workflow` enforce roles, the <=5000 tier and
the self-approval block, so this module only fetches + relays actions.
"""

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_transitions
from frappe.utils import flt, fmt_money, formatdate, get_fullname

# This page is for APPROVAL users only - it surfaces just the approval stage.
# Physical payout (Mark Paid on the 'Unpaid' state) is done from the ERPNext Desk
# by the maker, not here.
PENDING_STATES = ("Pending Approval",)
DOCTYPES = ("Payment Entry", "Journal Entry")
ACTIONS = ("Approve", "Reject")


def _je_detail(doc):
    debit, credit = None, None
    for row in doc.accounts:
        if flt(row.debit_in_account_currency) > 0 and not debit:
            debit = row.account
        if flt(row.credit_in_account_currency) > 0 and not credit:
            credit = row.account
    parts = []
    if debit:
        parts.append(debit)
    if credit:
        parts.append("from " + credit)
    return doc.user_remark or " ".join(parts)


def _summarize(doc, actions):
    if doc.doctype == "Payment Entry":
        amount = flt(doc.paid_amount)
        primary = " · ".join(filter(None, [doc.payment_type, doc.mode_of_payment]))
        party = doc.party_name or doc.party or ""
        flow = " → ".join(filter(None, [doc.paid_from, doc.paid_to]))
        secondary = " · ".join(filter(None, [party, flow]))
        remarks = doc.remarks
    else:
        amount = flt(doc.total_debit)
        primary = doc.voucher_type
        secondary = _je_detail(doc)
        # user_remark is often blank on JEs; the description usually lives in
        # the Reference Number (cheque_no). Fall back so the card is useful.
        remarks = doc.user_remark or doc.cheque_no

    return {
        "doctype": doc.doctype,
        "name": doc.name,
        "amount": amount,
        "amount_str": fmt_money(amount, currency=frappe.defaults.get_global_default("currency") or "INR"),
        "date": formatdate(doc.posting_date),
        "date_sort": str(doc.posting_date or ""),
        "primary": primary,
        "secondary": secondary,
        "remarks": (remarks or "").strip(),
        "created_by": get_fullname(doc.owner),
        "state": doc.workflow_state,
        # which stage this card sits at, so the UI can label it
        "stage": "payment" if doc.workflow_state == "Unpaid" else "approval",
        "actions": actions,
    }


@frappe.whitelist()
def get_pending_approvals():
    """Cash docs awaiting approval that the CURRENT user can act on.

    Only the 'Pending Approval' stage (Approve/Reject) - this page is for
    approval users. get_transitions still self-filters by role and the <=5000
    tier, so each approver sees only what they may act on.
    """
    items = []
    for dt in DOCTYPES:
        names = frappe.get_all(
            dt, filters={"workflow_state": ["in", PENDING_STATES], "docstatus": 0}, pluck="name"
        )
        for name in names:
            doc = frappe.get_doc(dt, name)
            try:
                actions = [t.action for t in get_transitions(doc) if t.action in ACTIONS]
            except Exception:
                # a single unreadable/odd doc must not break the whole list
                frappe.clear_last_message()
                continue
            if actions:
                items.append(_summarize(doc, actions))

    items.sort(key=lambda x: (x["date_sort"], x["name"]), reverse=True)
    return {
        "user": frappe.session.user,
        "fullname": get_fullname(frappe.session.user),
        "count": len(items),
        "items": items,
    }


@frappe.whitelist()
def get_recent_approved(limit=20):
    """Last N cash entries that have cleared approval.

    Spans both post-approval states so an approver can see what they approved
    and whether it has been physically paid yet:
      - 'Unpaid' (approved, awaiting payout)  -> status "To Pay"
      - 'Paid'   (cash disbursed, GL posted)  -> status "Paid"
    Non-cash direct-submits land in 'Approved', so they are excluded.
    """
    limit = min(int(limit or 20), 50)
    states = ["Unpaid", "Paid"]
    rows = []

    pe = frappe.get_all(
        "Payment Entry",
        filters={"workflow_state": ["in", states],
                 "payment_type": "Pay", "mode_of_payment": ["in", ["Cash", "Petty Cash"]]},
        fields=["name", "paid_amount", "payment_type", "mode_of_payment", "workflow_state",
                "party_name", "party", "posting_date", "owner", "modified"],
        order_by="modified desc", limit=limit,
    )
    for d in pe:
        rows.append({
            "doctype": "Payment Entry", "name": d.name, "amount": flt(d.paid_amount),
            "primary": " · ".join(filter(None, [d.payment_type, d.mode_of_payment])),
            "secondary": d.party_name or d.party or "",
            "state": d.workflow_state, "owner": d.owner, "modified": d.modified,
            "posting_date": d.posting_date,
        })

    je = frappe.get_all(
        "Journal Entry",
        filters={"workflow_state": ["in", states], "custom_is_cash_outflow": 1},
        fields=["name", "total_debit", "voucher_type", "cheque_no", "user_remark",
                "workflow_state", "posting_date", "owner", "modified"],
        order_by="modified desc", limit=limit,
    )
    for d in je:
        rows.append({
            "doctype": "Journal Entry", "name": d.name, "amount": flt(d.total_debit),
            "primary": d.voucher_type,
            "secondary": d.user_remark or d.cheque_no or "",
            "state": d.workflow_state, "owner": d.owner, "modified": d.modified,
            "posting_date": d.posting_date,
        })

    rows.sort(key=lambda x: str(x["modified"] or ""), reverse=True)
    rows = rows[:limit]

    currency = frappe.defaults.get_global_default("currency") or "INR"
    return {
        "items": [{
            "doctype": r["doctype"], "name": r["name"],
            "amount_str": fmt_money(r["amount"], currency=currency),
            "primary": r["primary"], "secondary": r["secondary"],
            "date": formatdate(r["posting_date"]),
            "status": "Paid" if r["state"] == "Paid" else "To Pay",
            "created_by": get_fullname(r["owner"]),
        } for r in rows]
    }


@frappe.whitelist()
def apply_action(doctype, name, action, reason=None):
    """Approve or Reject a single doc via the workflow engine."""
    if doctype not in DOCTYPES:
        frappe.throw(_("Unsupported document type"))
    if action not in ACTIONS:
        frappe.throw(_("Invalid action"))

    doc = frappe.get_doc(doctype, name)
    # apply_workflow enforces role + condition + self-approval; raises on violation
    apply_workflow(doc.as_json(), action)

    if action == "Reject" and reason:
        frappe.get_doc(doctype, name).add_comment(
            "Comment", _("Rejected via Approvals app: {0}").format(frappe.utils.strip_html(reason))
        )

    frappe.db.commit()
    new_state = frappe.db.get_value(doctype, name, "workflow_state")
    return {"ok": True, "doctype": doctype, "name": name, "action": action, "state": new_state}
