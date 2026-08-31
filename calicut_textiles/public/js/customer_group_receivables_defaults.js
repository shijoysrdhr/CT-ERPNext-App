// Opening defaults for the "Customer Group Receivables" report.
//
// That report is a Custom Report — a saved variant of Accounts Receivable — so
// it has no script of its own. Frappe loads accounts_receivable.js and files the
// same settings object under both names, which is why everything below is scoped
// by report name: unscoped, it would also fire on plain Accounts Receivable and
// on the other custom reports built from it.

frappe.provide("frappe.query_reports");

(() => {
	const REPORT = "Customer Group Receivables";
	const REFERENCE_REPORT = "Accounts Receivable";
	const STORAGE_KEY = "ct_customer_group_receivables:customer_group";

	function get_remembered_group() {
		try {
			const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
			return Array.isArray(saved) ? saved : [];
		} catch (e) {
			// Never set, or unreadable — open with a blank group.
			return [];
		}
	}

	function remember_group(value) {
		try {
			localStorage.setItem(STORAGE_KEY, JSON.stringify(value || []));
		} catch (e) {
			// Private browsing or a full quota: the defaults still apply, they
			// just won't carry over to the next visit.
		}
	}

	function apply_defaults(query_report) {
		if (query_report.report_name !== REPORT) return;

		// onload runs with _no_refresh set, so these land before the first run
		// rather than each triggering one of its own.
		const defaults = {
			report_date: frappe.datetime.get_today(),
			ageing_based_on: "Posting Date",
			customer_group: get_remembered_group(),
		};

		for (const [fieldname, value] of Object.entries(defaults)) {
			const filter = query_report.get_filter(fieldname);
			if (filter) filter.set_value(value);
		}

		// Remember whatever group is picked, for the next visit. on_change
		// replaces the built-in refresh branch, so re-run the report ourselves.
		const customer_group = query_report.get_filter("customer_group");
		if (customer_group) {
			customer_group.on_change = (report) => {
				remember_group(customer_group.get_value());
				if (!report._no_refresh) report.refresh(true);
			};
		}
	}

	function with_defaults(settings) {
		if (!settings || settings.ct_defaults_applied) return settings;
		settings.ct_defaults_applied = true;

		const onload = settings.onload;
		settings.onload = function (query_report) {
			if (onload) onload.call(this, query_report);
			apply_defaults(query_report);
		};
		return settings;
	}

	// accounts_receivable.js is fetched the first time a report built on it is
	// opened, so wrap the settings object at the moment it is assigned.
	let settings = with_defaults(frappe.query_reports[REFERENCE_REPORT]);
	Object.defineProperty(frappe.query_reports, REFERENCE_REPORT, {
		configurable: true,
		enumerable: true,
		get: () => settings,
		set: (value) => (settings = with_defaults(value)),
	});
})();
