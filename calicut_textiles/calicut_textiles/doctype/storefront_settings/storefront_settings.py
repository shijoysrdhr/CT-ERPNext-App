# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class StorefrontSettings(Document):
	pass


def get_settings():
	"""Convenience accessor used by the storefront API modules."""
	return frappe.get_cached_doc("Storefront Settings", "Storefront Settings")
