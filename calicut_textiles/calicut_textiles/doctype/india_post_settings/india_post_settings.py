# Copyright (c) 2026, Calicut Textiles and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document


class IndiaPostSettings(Document):
	def validate(self):
		checks = [
			("customer_id", r"\d{10}", _("Customer ID must be 10 digits")),
			("contract_sp_doc", r"\d{8}", _("Contract ids must be 8 digits")),
			("contract_sp_parcel", r"\d{8}", _("Contract ids must be 8 digits")),
			("contract_business_parcel", r"\d{8}", _("Contract ids must be 8 digits")),
			("sender_pincode", r"\d{6}", _("Sender PIN code must be 6 digits")),
			("booking_office_pincode", r"\d{6}", _("Office PIN code must be 6 digits")),
			("booking_office_id", r"\d{8}", _("Office ID must be 8 digits")),
			("sender_mobile", r"[6-9]\d{9}", _("Sender mobile must be 10 digits starting 6–9")),
		]
		for field, pattern, message in checks:
			value = (self.get(field) or "").strip()
			self.set(field, value)
			if value and not re.fullmatch(pattern, value):
				frappe.throw(message)
		for field in ("sender_address_line1", "sender_address_line2", "sender_address_line3", "sender_city"):
			value = (self.get(field) or "").strip()
			if value and not 3 <= len(value) <= 80:
				frappe.throw(_("{0} must be 3–80 characters").format(self.meta.get_label(field)))
		self.base_url = (self.base_url or "").strip().rstrip("/")

	def on_update(self):
		# New credentials or host: never reuse a token issued for the old ones.
		from calicut_textiles.calicut_textiles.india_post import clear_token

		clear_token()
