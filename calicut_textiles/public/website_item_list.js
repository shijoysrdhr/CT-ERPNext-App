frappe.listview_settings["Website Item"] = {
	add_fields: ["custom_batch_no", "website_warehouse", "name"],

	onload(listview) {
		listview._batch_qty_cache = {};
		this._refresh_batch_qty(listview);

		// Re-fetch whenever the list re-renders (filter change, paginate, refresh)
		const original_render = listview.render_list.bind(listview);
		listview.render_list = (...args) => {
			const result = original_render(...args);
			this._refresh_batch_qty(listview);
			return result;
		};
	},

	_refresh_batch_qty(listview) {
		const rows = listview.data || [];
		const names = rows
			.filter((r) => r.custom_batch_no)
			.map((r) => r.name);
		if (!names.length) return;

		frappe.call({
			method: "calicut_textiles.api.webshop.get_batch_qty_for_website_items",
			args: { names: JSON.stringify(names) },
			callback: (r) => {
				if (!r.message) return;
				const qty_map = r.message;
				listview._batch_qty_cache = qty_map;
				this._paint_batch_qty(listview, qty_map);
			},
		});
	},

	_paint_batch_qty(listview, qty_map) {
		const $rows = listview.$result.find(".list-row-container");
		$rows.each(function () {
			const $row = $(this);
			const row_name = $row.attr("data-name");
			if (!row_name || !(row_name in qty_map)) return;

			const qty = qty_map[row_name];
			let $badge = $row.find(".ct-batch-qty-badge");
			if (!$badge.length) {
				$badge = $(
					`<span class="ct-batch-qty-badge" style="display:inline-block;margin-left:8px;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500"></span>`
				);
				$row.find(".level-left .list-row-col").first().append($badge);
			}
			const color = qty > 0 ? "var(--green-100, #d1fadf)" : "var(--red-100, #fee4e2)";
			const text_color = qty > 0 ? "var(--green-700, #027a48)" : "var(--red-700, #b42318)";
			$badge.css({ background: color, color: text_color });
			$badge.text(`${qty} in stock`);
		});
	},
};
