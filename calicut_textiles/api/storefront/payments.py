"""Razorpay payment integration for storefront orders.

Two whitelisted methods:
  - create_razorpay_order  — called by the storefront before opening
    Razorpay Checkout. Returns the order_id + the public key_id so the
    browser can initialise the modal.
  - verify_razorpay_signature — utility callable; the actual signature
    check also happens inline inside `orders.place_order` so the order
    can only be created with a verified payment.

Keys are read from site config (`razorpay_key_id`, `razorpay_key_secret`).
Set them on Frappe Cloud → site → Configuration, or via
`bench --site <site> set-config razorpay_key_id ...`.
"""

import frappe
from frappe import _

try:
	import razorpay
except ImportError:  # razorpay package not installed yet on the bench
	razorpay = None


def _client():
	if razorpay is None:
		frappe.throw(_("razorpay package is not installed on the bench"))
	key_id = frappe.conf.get("razorpay_key_id")
	key_secret = frappe.conf.get("razorpay_key_secret")
	if not key_id or not key_secret:
		frappe.throw(_("Razorpay keys not configured. Set razorpay_key_id and razorpay_key_secret in site config."))
	return razorpay.Client(auth=(key_id, key_secret)), key_id


@frappe.whitelist(allow_guest=True)
def create_razorpay_order(amount, currency="INR", receipt=None):
	"""Create a Razorpay Order and return its ID + the public key_id.

	`amount` is in major units (rupees). Razorpay expects paise; we convert.
	"""
	client, key_id = _client()

	amount_in_paise = int(round(float(amount) * 100))
	if amount_in_paise <= 0:
		frappe.throw(_("Invalid amount"))

	rp_order = client.order.create({
		"amount": amount_in_paise,
		"currency": currency,
		"receipt": receipt or frappe.generate_hash(length=12),
		"payment_capture": 1,
	})

	return {
		"orderId": rp_order["id"],
		"amount": rp_order["amount"],
		"currency": rp_order["currency"],
		"keyId": key_id,
	}


def verify_signature(payment_id, order_id, signature):
	"""Raise if the Razorpay payment signature is invalid."""
	client, _key_id = _client()
	try:
		client.utility.verify_payment_signature({
			"razorpay_order_id": order_id,
			"razorpay_payment_id": payment_id,
			"razorpay_signature": signature,
		})
	except Exception as exc:
		frappe.log_error(
			title="Razorpay signature verification failed",
			message=f"order={order_id} payment={payment_id}: {exc}",
		)
		frappe.throw(_("Payment verification failed"))
