"""CrossChex Cloud -> Employee Checkin sync.

The HR manager adds and edits punch times directly in CrossChex (roughly 8% of
records come back with source=3, i.e. entered by hand), so this is a PERIOD
RE-SYNC rather than an incremental pull: for the requested range we fetch every
record, upsert on the record uuid, and delete any Employee Checkin whose uuid has
since disappeared upstream. Running it repeatedly over the same period is safe
and is the intended way to pick up corrections before payroll is processed.

Credentials live in site_config.json, not in the database or this repo:
    "crosschex_api_url":    "https://api.eu.crosschexcloud.com/",
    "crosschex_api_key":    "...",
    "crosschex_api_secret": "..."

API quirks worth knowing (verified against the live account):
  - the auth token goes in a top-level "authorize" block, NOT in the header;
    putting it in the header returns MISS_PARAM
  - one request per 15 seconds, else FREQUENT_REQUEST
  - per_page is capped at 200 however much you ask for
  - checktype is 0 on every record: CrossChex does not distinguish IN from OUT,
    so log_type is derived downstream by events.employee_checkin
  - checktime is UTC
"""

import time
import uuid as uuidlib
from datetime import datetime, timedelta, timezone

import frappe
import requests
from frappe import _
from frappe.utils import add_days, convert_utc_to_system_timezone, get_datetime, getdate

PER_PAGE = 200
MIN_REQUEST_GAP = 15  # seconds; CrossChex rejects anything faster
REQUEST_TIMEOUT = 90
TOKEN_CACHE_KEY = "crosschex_token"
LAST_CALL_CACHE_KEY = "crosschex_last_call"


# ---------------------------------------------------------------- transport


def _config():
    url = frappe.conf.get("crosschex_api_url")
    key = frappe.conf.get("crosschex_api_key")
    secret = frappe.conf.get("crosschex_api_secret")
    if not (url and key and secret):
        frappe.throw(
            _("CrossChex credentials are not configured. Set crosschex_api_url, "
              "crosschex_api_key and crosschex_api_secret in site_config.json.")
        )
    return url, key, secret


def _throttle():
    """Keep at least MIN_REQUEST_GAP seconds between calls to the API."""
    cache = frappe.cache()
    last = cache.get_value(LAST_CALL_CACHE_KEY)
    if last:
        elapsed = time.time() - float(last)
        if elapsed < MIN_REQUEST_GAP:
            time.sleep(MIN_REQUEST_GAP - elapsed)
    cache.set_value(LAST_CALL_CACHE_KEY, str(time.time()))


def _call(name_space, name_action, payload, token=None):
    url, _key, _secret = _config()
    body = {
        "header": {
            "nameSpace": name_space,
            "nameAction": name_action,
            "version": "1.0",
            "requestId": str(uuidlib.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "payload": payload,
    }
    if token:
        body["authorize"] = {"type": "token", "token": token}

    _throttle()
    response = requests.post(url, json=body, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()

    if data.get("header", {}).get("name") == "Exception":
        frappe.throw(
            _("CrossChex API error: {0}").format(data.get("payload", {}).get("message", data))
        )
    return data.get("payload", {})


def get_token(force=False):
    """Fetch (and briefly cache) an API token. CrossChex tokens last ~2 hours."""
    cache = frappe.cache()
    if not force:
        cached = cache.get_value(TOKEN_CACHE_KEY)
        if cached:
            return cached

    _url, key, secret = _config()
    payload = _call("authorize.token", "token", {"api_key": key, "api_secret": secret})
    token = payload.get("token")
    if not token:
        frappe.throw(_("CrossChex did not return a token."))

    cache.set_value(TOKEN_CACHE_KEY, token, expires_in_sec=45 * 60)
    return token


def fetch_records(from_date, to_date):
    """Every attendance record between the two dates, inclusive."""
    token = get_token()
    begin = f"{getdate(from_date)}T00:00:00+05:30"
    # end is exclusive upstream, so ask for the start of the following day
    end = f"{add_days(getdate(to_date), 1)}T00:00:00+05:30"

    records, page = [], 1
    while True:
        payload = _call(
            "attendance.record",
            "getrecord",
            {
                "begin_time": begin,
                "end_time": end,
                "order": "asc",
                "page": page,
                "per_page": PER_PAGE,
            },
            token=token,
        )
        records.extend(payload.get("list") or [])
        page_count = payload.get("pageCount") or 1
        if page >= page_count:
            break
        page += 1

    return records


# ---------------------------------------------------------------- sync


def _employee_by_workno():
    rows = frappe.get_all(
        "Employee",
        filters={"status": "Active", "attendance_device_id": ["is", "set"]},
        fields=["name", "attendance_device_id"],
    )
    return {str(r.attendance_device_id).strip(): r.name for r in rows}


def _local_time(checktime):
    """CrossChex returns UTC; store in the site's timezone."""
    return convert_utc_to_system_timezone(
        datetime.fromisoformat(checktime).astimezone(timezone.utc).replace(tzinfo=None)
    ).replace(tzinfo=None)


@frappe.whitelist()
def sync_checkins(from_date, to_date, delete_missing=True):
    """Re-sync every CrossChex record in the period into Employee Checkin.

    Safe to re-run: matches on the CrossChex uuid, so corrections update the
    existing check-in rather than creating a duplicate.
    """
    from_date, to_date = getdate(from_date), getdate(to_date)
    if from_date > to_date:
        frappe.throw(_("From Date must not be after To Date."))

    records = fetch_records(from_date, to_date)
    employees = _employee_by_workno()

    created = updated = unchanged = 0
    unmapped = {}
    seen_uuids = set()

    for record in records:
        record_uuid = record.get("uuid")
        workno = str((record.get("employee") or {}).get("workno") or "").strip()
        if not record_uuid or not workno:
            continue

        employee = employees.get(workno)
        if not employee:
            name = (record.get("employee") or {}).get("last_name") or ""
            unmapped[workno] = name
            continue

        seen_uuids.add(record_uuid)
        checktime = _local_time(record["checktime"])

        existing = frappe.db.get_value(
            "Employee Checkin",
            {"custom_crosschex_uuid": record_uuid},
            ["name", "time"],
            as_dict=True,
        )

        if existing:
            if get_datetime(existing.time) == checktime:
                unchanged += 1
                continue
            # HR corrected the punch upstream -- saving re-runs the late/early
            # recalculation in events.employee_checkin.
            doc = frappe.get_doc("Employee Checkin", existing.name)
            doc.time = checktime
            doc.flags.ignore_permissions = True
            doc.save(ignore_permissions=True)
            updated += 1
        else:
            doc = frappe.new_doc("Employee Checkin")
            doc.employee = employee
            doc.time = checktime
            doc.custom_crosschex_uuid = record_uuid
            doc.device_id = (record.get("device") or {}).get("serial_number") or None
            doc.flags.ignore_permissions = True
            doc.insert(ignore_permissions=True)
            created += 1

    deleted = 0
    if delete_missing:
        # A punch the HR manager removed upstream must disappear here too.
        stale = frappe.get_all(
            "Employee Checkin",
            filters={
                "time": ["between", [f"{from_date} 00:00:00", f"{to_date} 23:59:59"]],
                "custom_crosschex_uuid": ["is", "set"],
            },
            fields=["name", "custom_crosschex_uuid"],
        )
        for row in stale:
            if row.custom_crosschex_uuid not in seen_uuids:
                frappe.delete_doc(
                    "Employee Checkin", row.name, ignore_permissions=True, delete_permanently=True
                )
                deleted += 1

    frappe.db.commit()

    summary = {
        "from_date": str(from_date),
        "to_date": str(to_date),
        "fetched": len(records),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "deleted": deleted,
        "unmapped_worknos": unmapped,
    }
    frappe.logger("crosschex").info(summary)
    return summary


@frappe.whitelist()
def enqueue_sync(from_date, to_date):
    """Queue a sync -- a month spans several pages at 15s apart, far too slow
    to run inside a web request."""
    frappe.enqueue(
        sync_checkins,
        queue="long",
        timeout=3600,
        from_date=from_date,
        to_date=to_date,
    )
    frappe.msgprint(
        _("Check-in sync queued for {0} to {1}. It runs in the background.").format(
            from_date, to_date
        ),
        alert=True,
        indicator="blue",
    )


def sync_recent():
    """Daily catch-up. Re-reads the trailing window rather than only new records,
    so punches edited after the fact are corrected here too."""
    if not frappe.conf.get("crosschex_api_key"):
        return
    days = frappe.conf.get("crosschex_sync_days") or 10
    to_date = getdate()
    sync_checkins(add_days(to_date, -int(days)), to_date)
