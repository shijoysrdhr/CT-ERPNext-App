"""Storefront banners API — backs the home-page carousel.

Called via `/api/method/calicut_textiles.api.storefront.banners.list`.
Returns every enabled Storefront Banner, ordered by sort_order. Each
banner has its own image + overlay text + CTA, so the storefront just
renders them in sequence.

Response shape matches the `Banner` type in `src/lib/api/types.ts`.
"""

import frappe


@frappe.whitelist(allow_guest=True)
def list():
	rows = frappe.get_all(
		"Storefront Banner",
		filters={"enabled": 1},
		fields=[
			"name",
			"subtitle",
			"title",
			"description",
			"cta_label",
			"cta_link",
			"image",
			"image_alt",
			"theme",
		],
		order_by="sort_order ASC, creation ASC",
	)
	return [
		{
			"name": r.name,
			"subtitle": r.subtitle or None,
			"title": r.title or None,
			"description": r.description or None,
			"ctaLabel": r.cta_label or None,
			"ctaLink": r.cta_link or None,
			"imageUrl": r.image or None,
			"imageAlt": r.image_alt or None,
			"theme": r.theme or "Light text on image",
		}
		for r in rows
	]
