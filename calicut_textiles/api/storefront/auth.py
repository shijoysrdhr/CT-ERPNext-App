"""Phone-OTP authentication for the storefront.

Three public methods:
  - request_otp(phone)            — generate + SMS a 6-digit OTP
  - verify_otp(phone, otp)        — exchange OTP for a session token + customer
  - me()                          — resolve the current session token to a customer
  - logout()                      — delete the current session

Session tokens come in via the `X-Storefront-Token` HTTP header (or the
`storefront_token` form param as a fallback for environments that strip
custom headers). The token is a random URL-safe string; we never store the
raw value — only `sha256(token)` in `Storefront Session.token_hash`.

OTPs are stored hashed too. Rate limits:
  - 1 OTP request per phone per 60s
  - 5 verify attempts per OTP before invalidation
  - 10-minute OTP validity

If SMS Settings aren't configured, the OTP is logged to the Frappe Error
Log (visible to administrators) so dev testing still works without an SMS
gateway.
"""

import hashlib
import secrets
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import now_datetime, add_to_date

OTP_VALIDITY_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_REQUEST_COOLDOWN_SECONDS = 60
SESSION_VALIDITY_DAYS = 90
PHONE_DIGITS_MIN = 7
PHONE_DIGITS_MAX = 15

_HEADER = "X-Storefront-Token"


# ---------------------------------------------------------------------------
# Whitelisted endpoints
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
def request_otp(phone):
	"""Generate an OTP for the given phone, store its hash, and send via SMS."""
	phone = _normalise_phone(phone)

	# Rate limit: reject a fresh OTP request if a non-expired one was sent
	# within the cooldown window.
	cutoff = add_to_date(now_datetime(), seconds=-OTP_REQUEST_COOLDOWN_SECONDS, as_datetime=True)
	recent = frappe.db.exists(
		"Storefront OTP",
		{
			"phone": phone,
			"creation": (">=", cutoff),
		},
	)
	if recent:
		frappe.throw(_("Please wait a moment before requesting another code."))

	code = f"{secrets.randbelow(1_000_000):06d}"
	doc = frappe.new_doc("Storefront OTP")
	doc.phone = phone
	doc.code_hash = _sha256(code)
	doc.expires_on = add_to_date(
		now_datetime(), minutes=OTP_VALIDITY_MINUTES, as_datetime=True
	)
	doc.attempts = 0
	doc.consumed = 0
	doc.created_ip = _client_ip()
	doc.insert(ignore_permissions=True)

	_send_otp_sms(phone, code)

	return {"sent": True, "expiresInMinutes": OTP_VALIDITY_MINUTES}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def verify_otp(phone, otp):
	"""Exchange an OTP for a session token + customer profile."""
	phone = _normalise_phone(phone)
	otp = (otp or "").strip()
	if not otp.isdigit() or len(otp) != 6:
		frappe.throw(_("Enter the 6-digit code"))

	candidate = frappe.db.get_value(
		"Storefront OTP",
		filters={
			"phone": phone,
			"consumed": 0,
			"expires_on": (">", now_datetime()),
		},
		fieldname=["name", "code_hash", "attempts"],
		order_by="creation desc",
		as_dict=True,
	)
	if not candidate:
		frappe.throw(_("Code expired. Please request a new one."))

	if candidate.attempts >= OTP_MAX_ATTEMPTS:
		# Burn the doc so it can't be retried further.
		frappe.db.set_value("Storefront OTP", candidate.name, "consumed", 1)
		frappe.throw(_("Too many attempts. Please request a new code."))

	if _sha256(otp) != candidate.code_hash:
		frappe.db.set_value(
			"Storefront OTP", candidate.name, "attempts", candidate.attempts + 1
		)
		frappe.throw(_("That code didn't match. Try again."))

	# OTP valid — burn it.
	frappe.db.set_value(
		"Storefront OTP",
		candidate.name,
		{"consumed": 1, "consumed_at": now_datetime()},
		update_modified=False,
	)

	customer = _get_or_create_customer_by_phone(phone)
	token, session_name = _create_session(customer, phone)

	return {
		"token": token,
		"customer": _serialize_customer(customer),
		"sessionName": session_name,
	}


@frappe.whitelist(allow_guest=True)
def me():
	"""Return the Customer attached to the request's session token."""
	session = _resolve_session(required=True)
	frappe.db.set_value(
		"Storefront Session",
		session.name,
		"last_used_at",
		now_datetime(),
		update_modified=False,
	)
	customer = frappe.get_cached_doc("Customer", session.customer)
	return _serialize_customer(customer.name, customer=customer)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def logout():
	"""Delete the current session. Idempotent — succeeds even if already logged out."""
	session = _resolve_session(required=False)
	if session:
		frappe.delete_doc(
			"Storefront Session", session.name, ignore_permissions=True, force=True
		)
	return {"ok": True}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _normalise_phone(phone):
	if not phone:
		frappe.throw(_("Phone number is required"))
	digits = "".join(ch for ch in str(phone) if ch.isdigit() or ch == "+")
	if not digits or len([c for c in digits if c.isdigit()]) < PHONE_DIGITS_MIN:
		frappe.throw(_("Enter a valid phone number"))
	if len([c for c in digits if c.isdigit()]) > PHONE_DIGITS_MAX:
		frappe.throw(_("Enter a valid phone number"))
	return digits


def _sha256(value):
	return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _client_ip():
	try:
		return frappe.local.request_ip
	except Exception:
		return ""


def _user_agent():
	try:
		return frappe.get_request_header("User-Agent") or ""
	except Exception:
		return ""


def _send_otp_sms(phone, code):
	"""Try the configured SMS gateway. If SMS Settings isn't configured, or
	the gateway throws, fall back to logging the OTP to the Error Log so an
	admin can recover the code during setup."""
	msg = f"Your Calicut Textiles login code is {code}. Valid for {OTP_VALIDITY_MINUTES} minutes."

	gateway_url = frappe.db.get_single_value("SMS Settings", "sms_gateway_url")
	if not gateway_url:
		# Frappe's send_sms() silently msgprints when SMS Settings is empty
		# instead of raising — so we'd never hit the except branch. Log
		# explicitly so the OTP is recoverable during initial setup.
		frappe.log_error(
			title="Storefront OTP (SMS Settings empty — logging code)",
			message=f"Phone: {phone}\nCode: {code}",
		)
		return

	try:
		from frappe.core.doctype.sms_settings.sms_settings import send_sms

		send_sms([phone], msg)
	except Exception as exc:
		frappe.log_error(
			title="Storefront OTP (SMS send failed — falling back to log)",
			message=f"Phone: {phone}\nCode: {code}\nReason: {exc}",
		)


def _get_or_create_customer_by_phone(phone):
	"""Phone is the storefront's identity. Look up Customer by mobile_no; if
	none exists, create one with sensible defaults — preferring Storefront
	Settings, falling back to Selling Settings, then to the ERPNext root
	groups so OTP login works even before the admin has configured the
	storefront settings doctype."""
	existing = frappe.db.get_value("Customer", {"mobile_no": phone}, "name")
	if existing:
		return existing

	customer_group, territory, fallback_name = _customer_defaults()

	doc = frappe.new_doc("Customer")
	doc.customer_name = fallback_name
	doc.customer_type = "Individual"
	doc.customer_group = customer_group
	doc.territory = territory
	doc.mobile_no = phone
	doc.insert(ignore_permissions=True)
	return doc.name


def _customer_defaults():
	"""Resolve (customer_group, territory, fallback_name) with graceful fallbacks."""
	customer_group = None
	territory = None
	fallback_name = "Storefront Customer"

	try:
		from calicut_textiles.calicut_textiles.doctype.storefront_settings.storefront_settings import (
			get_settings,
		)
		settings = get_settings()
		customer_group = settings.default_customer_group or None
		territory = settings.default_territory or None
		fallback_name = settings.fallback_customer_name or fallback_name
	except Exception:
		pass

	if not customer_group:
		customer_group = (
			frappe.db.get_single_value("Selling Settings", "customer_group")
			or frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
			or "Individual"
		)
	if not territory:
		territory = (
			frappe.db.get_single_value("Selling Settings", "territory")
			or frappe.db.get_value("Territory", {"is_group": 0}, "name")
			or "All Territories"
		)
	return customer_group, territory, fallback_name


def _create_session(customer, phone):
	token = secrets.token_urlsafe(32)
	doc = frappe.new_doc("Storefront Session")
	doc.customer = customer
	doc.phone = phone
	doc.token_hash = _sha256(token)
	doc.expires_on = add_to_date(now_datetime(), days=SESSION_VALIDITY_DAYS, as_datetime=True)
	doc.last_used_at = now_datetime()
	doc.created_ip = _client_ip()
	doc.user_agent = _user_agent()[:1000]
	doc.insert(ignore_permissions=True)
	return token, doc.name


def _serialize_customer(name, customer=None):
	if customer is None:
		customer = frappe.get_cached_doc("Customer", name)
	return {
		"name": customer.name,
		"customerName": customer.customer_name,
		"email": customer.email_id or None,
		"phone": customer.mobile_no or None,
	}


def _resolve_session(required=True):
	"""Pull the session token off the request and return the matching session,
	or raise/None depending on `required`."""
	token = None
	try:
		token = frappe.get_request_header(_HEADER)
	except Exception:
		pass
	if not token:
		token = frappe.form_dict.get("storefront_token")
	if not token:
		if required:
			frappe.throw(_("Not signed in"), frappe.AuthenticationError)
		return None

	row = frappe.db.get_value(
		"Storefront Session",
		filters={"token_hash": _sha256(token)},
		fieldname=["name", "customer", "expires_on"],
		as_dict=True,
	)
	if not row:
		if required:
			frappe.throw(_("Session invalid or expired"), frappe.AuthenticationError)
		return None

	if row.expires_on and row.expires_on < now_datetime():
		# Clean up expired session.
		frappe.delete_doc(
			"Storefront Session", row.name, ignore_permissions=True, force=True
		)
		if required:
			frappe.throw(_("Session expired"), frappe.AuthenticationError)
		return None

	return row


def require_customer():
	"""Helper for other API modules — return the Customer name for the
	current session, raising AuthenticationError if not signed in."""
	session = _resolve_session(required=True)
	frappe.db.set_value(
		"Storefront Session",
		session.name,
		"last_used_at",
		now_datetime(),
		update_modified=False,
	)
	return session.customer
