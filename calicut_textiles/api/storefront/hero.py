"""Storefront hero API.

Returns the current home-page hero content from the `Storefront Hero` Single
doctype. Called via `/api/method/calicut_textiles.api.storefront.hero.get`.

Response shape matches the `Hero` type in `src/lib/api/types.ts`.
"""

import frappe


@frappe.whitelist(allow_guest=True)
def get():
	"""Return the current hero. Empty fields are returned as None so the
	storefront can fall back to its built-in defaults on a per-field basis."""
	try:
		doc = frappe.get_cached_doc("Storefront Hero", "Storefront Hero")
	except frappe.DoesNotExistError:
		return _empty()

	return {
		"subtitle": doc.subtitle or None,
		"title": doc.title or None,
		"description": doc.description or None,
		"ctaLabel": doc.cta_label or None,
		"ctaLink": doc.cta_link or None,
		"imageUrl": doc.image or None,
		"imageAlt": doc.image_alt or None,
	}


def _empty():
	return {
		"subtitle": None,
		"title": None,
		"description": None,
		"ctaLabel": None,
		"ctaLink": None,
		"imageUrl": None,
		"imageAlt": None,
	}
