"""GOShop install/setup helpers."""

import frappe


DEFAULT_CATEGORIES = [
	{"name": "Sarees", "parent": None, "route": "sarees", "is_group": 1, "weightage": 50},
	{"name": "Women", "parent": None, "route": "women", "is_group": 1, "weightage": 40},
	{"name": "Men", "parent": None, "route": "men", "is_group": 1, "weightage": 30},
	{"name": "Wedding", "parent": None, "route": "wedding", "is_group": 1, "weightage": 20},
	{"name": "Other", "parent": None, "route": "other", "is_group": 1, "weightage": 10},
	{"name": "Silk Sarees", "parent": "Sarees", "route": "sarees/silk-sarees", "is_group": 0, "weightage": 20},
	{"name": "Linen Sarees", "parent": "Sarees", "route": "sarees/linen-sarees", "is_group": 0, "weightage": 10},
]


def seed_default_categories():
	"""Create the default Storefront Category tree if it doesn't already exist.
	Idempotent — skips any category whose name is already present."""
	created = []
	for entry in DEFAULT_CATEGORIES:
		if frappe.db.exists("Storefront Category", entry["name"]):
			continue
		doc = frappe.get_doc({
			"doctype": "Storefront Category",
			"storefront_category_name": entry["name"],
			"parent_storefront_category": entry["parent"],
			"route": entry["route"],
			"is_group": entry["is_group"],
			"weightage": entry["weightage"],
		})
		doc.insert(ignore_permissions=True)
		created.append(entry["name"])
	if created:
		frappe.db.commit()
	return created


def after_install():
	"""Runs once on `bench install-app calicut_textiles`."""
	seed_default_categories()
