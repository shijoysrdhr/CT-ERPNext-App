"""Re-invoke webshop's `add_custom_fields()` setup hook.

When webshop is installed via Frappe Cloud's "Install App" flow on an existing
site, its `after_install()` hook doesn't always complete — the most visible
symptom is that `webshop.patches.clear_cache_for_item_group_route` fails on
the next migrate because `tabItem Group.route` was never created.

This patch retro-fits the missing custom fields by calling
`webshop.setup.install.add_custom_fields()` directly. It's idempotent —
`create_custom_field` no-ops for any field that already exists.

Listed in `patches.txt` BEFORE webshop's `clear_cache_for_item_group_route`
gets a chance to fail. (Calicut Textiles patches run before Webshop patches
because the app order in apps.txt puts calicut_textiles first.)
"""

import frappe


def execute():
	try:
		from webshop.setup.install import add_custom_fields
	except ImportError:
		# Webshop not installed — nothing to do.
		return
	add_custom_fields()
