# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class StorefrontShippingZone(Document):
	def validate(self):
		if self.is_default:
			# Make sure only one zone is the default.
			others = frappe.get_all(
				"Storefront Shipping Zone",
				filters={"is_default": 1, "name": ("!=", self.name)},
				pluck="name",
			)
			for other in others:
				frappe.db.set_value(
					"Storefront Shipping Zone", other, "is_default", 0, update_modified=False
				)

	def prefix_list(self):
		"""Parse `pincode_prefixes` into a clean list of strings."""
		raw = self.pincode_prefixes or ""
		parts = raw.replace("\n", ",").split(",")
		return [p.strip() for p in parts if p.strip()]


def resolve_rate(pincode, subtotal=0):
	"""Return (rate, zone_name) for the given pincode.

	Picks the zone whose longest prefix matches the leading characters of
	the pincode. Falls back to the zone marked `is_default`. Returns
	(0, None) if neither applies.

	`subtotal` controls free-shipping-above behaviour.
	"""
	pincode = str(pincode or "").strip()

	zones = frappe.get_all(
		"Storefront Shipping Zone",
		fields=["name", "zone_name", "rate", "pincode_prefixes", "is_default", "free_above"],
	)

	matched = None
	matched_prefix_len = -1
	default = None

	for zone in zones:
		if zone.is_default and not default:
			default = zone
		prefixes = _parse_prefixes(zone.pincode_prefixes)
		for prefix in prefixes:
			if pincode and pincode.startswith(prefix) and len(prefix) > matched_prefix_len:
				matched = zone
				matched_prefix_len = len(prefix)

	zone = matched or default
	if not zone:
		return 0.0, None

	if zone.free_above and float(subtotal or 0) >= float(zone.free_above):
		return 0.0, zone.zone_name

	return float(zone.rate or 0), zone.zone_name


def _parse_prefixes(raw):
	if not raw:
		return []
	parts = raw.replace("\n", ",").split(",")
	return [p.strip() for p in parts if p.strip()]
