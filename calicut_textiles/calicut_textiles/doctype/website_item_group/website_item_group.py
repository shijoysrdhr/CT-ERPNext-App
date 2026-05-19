# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe.utils.nestedset import NestedSet
from frappe.website.utils import clear_cache


class WebsiteItemGroup(NestedSet):
	nsm_parent_field = "parent_website_item_group"

	def autoname(self):
		# Autoname via field:website_item_group_name; nothing extra to do here.
		pass

	def validate(self):
		if not self.route:
			self.route = self._derive_route()

	def on_update(self):
		NestedSet.on_update(self)
		clear_cache()

	def on_trash(self):
		NestedSet.on_trash(self)
		clear_cache()

	def _derive_route(self):
		"""URL slug — kebab-case from the name. Prefixed with parent slug
		when the parent has a route, so nested groups get
		`sarees/silk-sarees` style paths."""
		slug = frappe.scrub(self.website_item_group_name).replace("_", "-")
		if self.parent_website_item_group:
			parent_route = frappe.db.get_value(
				"Website Item Group", self.parent_website_item_group, "route"
			)
			if parent_route:
				return f"{parent_route}/{slug}"
		return slug
