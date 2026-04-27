import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, today, date_diff, nowdate
from frappe.utils.nestedset import get_descendants_of

from erpnext.stock.report.batch_wise_balance_history.batch_wise_balance_history import (
    get_stock_ledger_entries_for_batch_no,
    get_stock_ledger_entries_for_batch_bundle,
)


def execute(filters=None):
    filters = frappe._dict(filters or {})
    columns = get_columns(filters)
    data = get_data(filters)
    return columns, data


def _precision():
    return cint(frappe.db.get_default("float_precision")) or 3


def get_columns(filters):
    show_batch = cint(filters.get("show_batch"))
    cols = [
        {"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
        {"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 220},
        {"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 140},
        {"label": _("Parent Item Group"), "fieldname": "parent_item_group", "fieldtype": "Link", "options": "Item Group", "width": 150},
        {"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 140},
    ]
    if show_batch:
        cols.append({"label": _("Batch"), "fieldname": "batch_no", "fieldtype": "Link", "options": "Batch", "width": 140})
    cols.extend([
        {"label": _("Valuation Rate"), "fieldname": "valuation_rate", "fieldtype": "Currency", "width": 120},
        {"label": _("Balance Qty"), "fieldname": "balance_qty", "fieldtype": "Float", "width": 110, "precision": 2},
        {"label": _("Balance Value"), "fieldname": "balance_value", "fieldtype": "Currency", "width": 130},
    ])
    if show_batch:
        cols.extend([
            {"label": _("Entered Via"), "fieldname": "voucher_type", "fieldtype": "Data", "width": 140},
            {"label": _("Document"), "fieldname": "voucher_no", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 170},
            {"label": _("Purchase Invoice"), "fieldname": "purchase_invoice", "fieldtype": "Link", "options": "Purchase Invoice", "width": 170},
            {"label": _("Payment Status"), "fieldname": "payment_status", "fieldtype": "Data", "width": 130},
            {"label": _("Entry Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 110},
            {"label": _("Age (Days)"), "fieldname": "age", "fieldtype": "Int", "width": 90},
            {"label": _("Ageing"), "fieldname": "age_bucket", "fieldtype": "Data", "width": 100},
            {"label": _("Supplier"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 220},
            {"label": _("Supplier Group"), "fieldname": "supplier_group", "fieldtype": "Link", "options": "Supplier Group", "width": 160},
        ])
    return cols


def age_bucket(days):
    if days is None:
        return ""
    if days <= 30:
        return "0-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    return "90+"


def get_data(filters):
    # User-facing from/to dates filter rows by each batch's entry date (earliest
    # inward movement). SLE fetch still scans full history so balances stay correct.
    entry_from_date = filters.pop("from_date", None)
    entry_to_date = filters.pop("to_date", None)
    filters["from_date"] = "1900-01-01"
    filters["to_date"] = filters.get("as_on_date") or nowdate()

    movements = get_stock_ledger_entries_for_batch_no(filters) + get_stock_ledger_entries_for_batch_bundle(filters)
    if not movements:
        return []

    precision = _precision()

    balances = {}
    for m in movements:
        if not m.get("batch_no"):
            continue
        key = (m["item_code"], m["batch_no"], m.get("warehouse"))
        b = balances.setdefault(key, {"qty": 0.0, "value": 0.0})
        b["qty"] += flt(m["actual_qty"])
        b["value"] += flt(m["stock_value_difference"])

    positive = {k: v for k, v in balances.items() if flt(v["qty"], precision) > 0}
    if not positive:
        return []

    item_filter = filters.get("item_group")
    item_map = get_item_info({k[0] for k in positive.keys()})
    if item_filter:
        allowed = set(get_descendants_of("Item Group", item_filter)) | {item_filter}
        positive = {k: v for k, v in positive.items() if item_map.get(k[0], {}).get("item_group") in allowed}
        if not positive:
            return []

    origin_map = get_batch_origins(set(positive.keys()))
    supplier_map = get_supplier_map(origin_map.values())
    pi_map = get_purchase_invoice_map(origin_map.values())

    today_d = getdate(today())
    rows = []
    for (item_code, batch_no, warehouse), bal in positive.items():
        origin = origin_map.get((item_code, batch_no, warehouse)) or {}
        item = item_map.get(item_code) or {}
        v_type = origin.get("voucher_type")
        v_no = origin.get("voucher_no")
        posting_date = origin.get("posting_date")
        valuation_rate = (bal["value"] / bal["qty"]) if bal["qty"] else 0
        balance_value = bal["qty"] * valuation_rate
        supplier = supplier_map.get((v_type, v_no)) or {}
        days = date_diff(today_d, posting_date) if posting_date else None

        rows.append({
            "item_code": item_code,
            "item_name": item.get("item_name"),
            "item_group": item.get("item_group"),
            "parent_item_group": item.get("parent_item_group"),
            "warehouse": warehouse,
            "batch_no": batch_no,
            "valuation_rate": flt(valuation_rate, precision),
            "balance_qty": flt(bal["qty"], precision),
            "balance_value": flt(balance_value, precision),
            "voucher_type": v_type,
            "voucher_no": v_no,
            "purchase_invoice": (pi_map.get((v_type, v_no)) or {}).get("name"),
            "payment_status": (pi_map.get((v_type, v_no)) or {}).get("status"),
            "posting_date": posting_date,
            "age": days,
            "age_bucket": age_bucket(days),
            "supplier_name": supplier.get("supplier_name") or supplier.get("supplier"),
            "supplier": supplier.get("supplier"),
            "supplier_group": supplier.get("supplier_group"),
        })

    rows = apply_supplier_filters(rows, filters)
    rows = apply_entry_date_filter(rows, entry_from_date, entry_to_date)
    rows = apply_ageing_filter(rows, filters.get("ageing"))
    rows = apply_entered_via_filter(rows, filters.get("entered_via"))
    rows = apply_payment_status_filter(rows, filters.get("payment_status"))

    if not cint(filters.get("show_batch")):
        rows = aggregate_by_item_warehouse(rows, precision)
        rows.sort(key=lambda r: (r["item_code"] or "", r.get("warehouse") or ""))
        return rows

    rows.sort(key=lambda r: (r["item_code"] or "", r.get("warehouse") or "", r["posting_date"] or getdate("1900-01-01")))
    return rows


def aggregate_by_item_warehouse(rows, precision):
    items = {}
    for r in rows:
        key = (r["item_code"], r.get("warehouse"))
        agg = items.setdefault(key, {
            "item_code": r["item_code"],
            "item_name": r.get("item_name"),
            "item_group": r.get("item_group"),
            "parent_item_group": r.get("parent_item_group"),
            "warehouse": r.get("warehouse"),
            "balance_qty": 0.0,
            "balance_value": 0.0,
        })
        agg["balance_qty"] += flt(r.get("balance_qty"))
        agg["balance_value"] += flt(r.get("balance_value"))
    for agg in items.values():
        qty = agg["balance_qty"]
        agg["valuation_rate"] = flt(agg["balance_value"] / qty, precision) if qty else 0
        agg["balance_qty"] = flt(qty, precision)
        agg["balance_value"] = flt(agg["balance_value"], precision)
    return list(items.values())


def apply_ageing_filter(rows, bucket):
    if not bucket:
        return rows
    return [r for r in rows if r.get("age_bucket") == bucket]


def apply_entered_via_filter(rows, voucher_type):
    if not voucher_type:
        return rows
    return [r for r in rows if r.get("voucher_type") == voucher_type]


def apply_payment_status_filter(rows, status):
    if not status:
        return rows
    return [r for r in rows if r.get("payment_status") == status]


def apply_entry_date_filter(rows, entry_from_date, entry_to_date):
    if not entry_from_date and not entry_to_date:
        return rows
    ef = getdate(entry_from_date) if entry_from_date else None
    et = getdate(entry_to_date) if entry_to_date else None
    out = []
    for r in rows:
        pd = r.get("posting_date")
        if not pd:
            continue
        d = getdate(pd)
        if ef and d < ef:
            continue
        if et and d > et:
            continue
        out.append(r)
    return out


def apply_supplier_filters(rows, filters):
    s = filters.get("supplier")
    sg = filters.get("supplier_group")
    if not s and not sg:
        return rows
    out = []
    for r in rows:
        if s and r.get("supplier") != s:
            continue
        if sg and r.get("supplier_group") != sg:
            continue
        out.append(r)
    return out


def get_batch_origins(batch_keys):
    """Return the earliest INWARD movement per (item_code, batch_no, warehouse) along with
    voucher_type, voucher_no, posting_date, incoming_rate.
    """
    if not batch_keys:
        return {}

    by_item = {}
    for ic, bn, _wh in batch_keys:
        by_item.setdefault(ic, set()).add(bn)

    origins = {}
    for item_code, batches in by_item.items():
        legacy = frappe.db.sql(
            """
            SELECT item_code, batch_no, warehouse, posting_date, posting_time, creation,
                   voucher_type, voucher_no, incoming_rate
            FROM `tabStock Ledger Entry`
            WHERE is_cancelled = 0
              AND docstatus < 2
              AND actual_qty > 0
              AND item_code = %(ic)s
              AND batch_no IN %(batches)s
            """,
            {"ic": item_code, "batches": tuple(batches)},
            as_dict=True,
        )
        bundled = frappe.db.sql(
            """
            SELECT sle.item_code, e.batch_no, sle.warehouse, sle.posting_date, sle.posting_time, sle.creation,
                   sle.voucher_type, sle.voucher_no, e.incoming_rate
            FROM `tabStock Ledger Entry` sle
            INNER JOIN `tabSerial and Batch Entry` e ON e.parent = sle.serial_and_batch_bundle
            WHERE sle.is_cancelled = 0
              AND sle.docstatus < 2
              AND sle.has_batch_no = 1
              AND e.qty > 0
              AND sle.item_code = %(ic)s
              AND e.batch_no IN %(batches)s
            """,
            {"ic": item_code, "batches": tuple(batches)},
            as_dict=True,
        )
        for r in legacy + bundled:
            key = (r["item_code"], r["batch_no"], r["warehouse"])
            sort_key = (r["posting_date"], r["posting_time"], r["creation"])
            existing = origins.get(key)
            if existing is None or sort_key < existing["_sort"]:
                origins[key] = {
                    "voucher_type": r["voucher_type"],
                    "voucher_no": r["voucher_no"],
                    "posting_date": r["posting_date"],
                    "incoming_rate": r["incoming_rate"],
                    "_sort": sort_key,
                }
    return origins


def get_item_info(item_codes):
    if not item_codes:
        return {}
    rows = frappe.db.sql(
        """
        SELECT i.name AS item_code, i.item_name, i.item_group
        FROM `tabItem` i
        WHERE i.name IN %(items)s
        """,
        {"items": list(item_codes)},
        as_dict=True,
    )
    top_parent_map = build_top_parent_map({r["item_group"] for r in rows if r.get("item_group")})
    for r in rows:
        r["parent_item_group"] = top_parent_map.get(r["item_group"])
    return {r["item_code"]: r for r in rows}


def build_top_parent_map(item_groups):
    """For each item_group, return its top-level ancestor (a node with no parent).

    Walks `parent_item_group` upward until a root (parent IS NULL) is reached.
    A root maps to itself.
    """
    if not item_groups:
        return {}

    rows = frappe.db.sql(
        """SELECT name, parent_item_group FROM `tabItem Group`""",
        as_dict=True,
    )
    parent_of = {r["name"]: r["parent_item_group"] for r in rows}

    cache = {}

    def walk(name):
        if name in cache:
            return cache[name]
        chain = []
        cur = name
        while cur is not None:
            if cur in cache:
                result = cache[cur]
                break
            parent = parent_of.get(cur)
            if not parent:
                result = cur
                break
            chain.append(cur)
            cur = parent
        else:
            result = name
        for n in chain:
            cache[n] = result
        cache[name] = result
        return result

    return {ig: walk(ig) for ig in item_groups}


def get_purchase_invoice_map(origins):
    """Return {(voucher_type, voucher_no): {"name": pi, "status": status}} for batch origins.

    PR origins resolve via `Purchase Invoice Item.purchase_receipt` to a submitted
    Purchase Invoice. If a PR is billed across multiple PIs, the earliest submitted
    PI (by posting_date, then creation) wins. PI origins map to themselves.
    """
    pr_names = set()
    pi_self = set()
    for o in origins:
        v_type = o.get("voucher_type")
        v_no = o.get("voucher_no")
        if not (v_type and v_no):
            continue
        if v_type == "Purchase Receipt":
            pr_names.add(v_no)
        elif v_type == "Purchase Invoice":
            pi_self.add(v_no)

    pi_map = {}

    if pi_self:
        for r in frappe.db.sql(
            """SELECT name, status FROM `tabPurchase Invoice` WHERE name IN %(v)s""",
            {"v": tuple(pi_self)},
            as_dict=True,
        ):
            pi_map[("Purchase Invoice", r["name"])] = {"name": r["name"], "status": r["status"]}

    if pr_names:
        rows = frappe.db.sql(
            """
            SELECT pii.purchase_receipt AS pr, pii.parent AS pi,
                   pi.posting_date, pi.creation, pi.status
            FROM `tabPurchase Invoice Item` pii
            INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
            WHERE pi.docstatus = 1
              AND pii.purchase_receipt IN %(prs)s
            """,
            {"prs": tuple(pr_names)},
            as_dict=True,
        )
        by_pr = {}
        for r in rows:
            sort_key = (r["posting_date"], r["creation"])
            existing = by_pr.get(r["pr"])
            if existing is None or sort_key < existing[0]:
                by_pr[r["pr"]] = (sort_key, r["pi"], r["status"])
        for pr, (_, pi, status) in by_pr.items():
            pi_map[("Purchase Receipt", pr)] = {"name": pi, "status": status}

    return pi_map


def get_supplier_map(origins):
    by_type = {}
    for o in origins:
        v_type = o.get("voucher_type")
        v_no = o.get("voucher_no")
        if v_type and v_no:
            by_type.setdefault(v_type, set()).add(v_no)

    supplier_doctypes = {
        "Purchase Receipt": "supplier",
        "Purchase Invoice": "supplier",
        "Stock Entry": "supplier",
        "Subcontracting Receipt": "supplier",
    }

    voucher_to_supplier = {}
    all_supplier_codes = set()
    for v_type, vouchers in by_type.items():
        field = supplier_doctypes.get(v_type)
        if not field or not frappe.db.exists("DocType", v_type):
            continue
        meta = frappe.get_meta(v_type)
        if not meta.has_field(field):
            continue
        rows = frappe.db.sql(
            f"""SELECT name, `{field}` AS supplier FROM `tab{v_type}` WHERE name IN %(v)s""",
            {"v": list(vouchers)},
            as_dict=True,
        )
        for r in rows:
            voucher_to_supplier[(v_type, r["name"])] = r["supplier"]
            if r["supplier"]:
                all_supplier_codes.add(r["supplier"])

    info_map = {}
    if all_supplier_codes:
        for r in frappe.db.sql(
            """SELECT name, supplier_name, supplier_group FROM `tabSupplier` WHERE name IN %(v)s""",
            {"v": list(all_supplier_codes)},
            as_dict=True,
        ):
            info_map[r["name"]] = r

    return {
        key: {
            "supplier": code,
            "supplier_name": (info_map.get(code) or {}).get("supplier_name") or code,
            "supplier_group": (info_map.get(code) or {}).get("supplier_group"),
        }
        for key, code in voucher_to_supplier.items()
    }
