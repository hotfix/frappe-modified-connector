const API = "modified_connector.modified_connector.doctype.modified_shop.modified_shop.";

function queue_action(frm, method, started, running) {
	frappe.call({
		method: API + method,
		type: "POST",
		args: { shop: frm.doc.name },
		freeze: true,
		callback: (r) => {
			const queued = r.message && r.message.queued;
			frappe.show_alert(
				{ message: queued ? started : running, indicator: queued ? "green" : "orange" },
				7
			);
		},
	});
}

// Keep the browser from filling the shop API password with the ERPNext one (Data inputs are "off" already).
function set_autocomplete(frm, fieldname, value) {
	const field = frm.get_field(fieldname);
	if (field && field.$input) field.$input.attr("autocomplete", value);
}

// -- shop data: pick shop IDs by name ------------------------------------------------------------
// The fields keep storing the shop ID; the loaded names (shop_options) only label and suggest values.
const ID_FIELDS = {
	cancelled_status_id: "Order Status",
	shipped_status_id: "Order Status",
	dhl_carrier_id: "Carrier",
};

function shop_options(frm, kind) {
	return (frm.doc.shop_options || [])
		.filter((row) => row.kind === kind && row.option_id)
		.map((row) => {
			// labels are shown as HTML (suggestion list, checkboxes, descriptions): escape shop texts
			const id = esc(row.option_id);
			const name = row.option_label ? esc(row.option_label) : id;
			let label = name === id ? id : `${name} (${id})`;
			if (kind === "Tax Rate") label = name === id ? `${id} %` : `${id} % – ${name}`;
			return { value: row.option_id, label: label };
		});
}

function esc(text) {
	return frappe.utils.escape_html(String(text));
}

function split_ids(value) {
	return (value || "")
		.split(",")
		.map((v) => v.trim())
		.filter(Boolean);
}

// Field description = its own (translated) description plus a line about the loaded shop data.
function set_extra_description(frm, fieldname, extra) {
	const field = frm.get_field(fieldname);
	if (!field) return;
	if (field.df.base_description === undefined) field.df.base_description = field.df.description || "";
	const base = __(field.df.base_description);
	frm.set_df_property(fieldname, "description", extra ? `${base}<br><b>${extra}</b>` : base);
}

function label_of(options, id) {
	const option = options.find((o) => o.value === String(id));
	return option ? option.label : null;
}

function apply_shop_options(frm) {
	const loaded = (frm.doc.shop_options || []).length > 0;
	const statuses = shop_options(frm, "Order Status");

	for (const [fieldname, kind] of Object.entries(ID_FIELDS)) {
		const field = frm.get_field(fieldname);
		if (!field) continue;
		const options = shop_options(frm, kind);
		field.df.ignore_validation = 1; // an ID that is not in the list can still be typed
		field.df.options = options;
		if (field.set_data) field.set_data(shop_options(frm, kind));
		frm.refresh_field(fieldname);
		const value = frm.doc[fieldname];
		set_extra_description(
			frm,
			fieldname,
			options.length && value && !label_of(options, value)
				? __("ID {0} is not in the loaded shop data.", [esc(value)])
				: ""
		);
	}

	const chosen = split_ids(frm.doc.import_status_ids);
	set_extra_description(
		frm,
		"import_status_ids",
		statuses.length && chosen.length
			? __("Selected: {0}", [
					chosen
						.map((id) => label_of(statuses, id) || __("ID {0} (not in the shop)", [esc(id)]))
						.join(", "),
			  ])
			: ""
	);
	frm.toggle_display("choose_import_statuses", statuses.length > 0);

	// Payment Map: suggest the shop's payment classes, any other code can still be typed.
	const payment_grid = frm.get_field("payment_map").grid;
	const payment_df = frappe.meta.get_docfield(
		"Modified Shop Payment Map",
		"payment_class",
		frm.doc.name
	);
	if (payment_df) {
		payment_df.options = shop_options(frm, "Payment Class");
		payment_df.ignore_validation = 1;
	}
	// rows already rendered have their own copy of the docfield
	if (payment_grid.grid_rows && payment_grid.grid_rows.length) {
		payment_grid.update_docfield_property(
			"payment_class",
			"options",
			shop_options(frm, "Payment Class")
		);
		payment_grid.update_docfield_property("payment_class", "ignore_validation", 1);
	}

	// Tax Map: list the shop's rates and offer to add the missing ones.
	const rates = shop_options(frm, "Tax Rate");
	set_extra_description(
		frm,
		"tax_map",
		rates.length
			? __("Tax rates in the shop: {0}", [rates.map((r) => r.label).join(", ")])
			: ""
	);
	set_extra_description(
		frm,
		"payment_map",
		loaded && shop_options(frm, "Payment Class").length
			? __("Payment classes in the shop: {0}", [
					shop_options(frm, "Payment Class")
						.map((p) => p.label)
						.join(", "),
			  ])
			: ""
	);
	const tax_grid = frm.get_field("tax_map").grid;
	const button = __("Add Shop Tax Rates");
	if (rates.length && frm.has_perm("write")) {
		tax_grid.add_custom_button(button, () => add_missing_tax_rates(frm));
	} else if (tax_grid.custom_buttons && tax_grid.custom_buttons[button]) {
		tax_grid.custom_buttons[button].addClass("hidden");
	}
}

function add_missing_tax_rates(frm) {
	const present = (frm.doc.tax_map || []).map((row) => flt(row.tax_rate));
	const missing = shop_options(frm, "Tax Rate").filter((r) => !present.includes(flt(r.value)));
	if (!missing.length) {
		frappe.show_alert({ message: __("All tax rates of the shop are in the Tax Map."), indicator: "green" });
		return;
	}
	for (const rate of missing) frm.add_child("tax_map", { tax_rate: flt(rate.value) });
	frm.refresh_field("tax_map");
	frappe.show_alert(
		{
			message: __("{0} rows added. Choose the Item Tax Template in each new row.", [missing.length]),
			indicator: "blue",
		},
		7
	);
}

function show_load_result(result) {
	const labels = {
		"Order Status": __("Order statuses"),
		Carrier: __("Carriers"),
		"Payment Class": __("Payment classes"),
		"Tax Rate": __("Tax rates"),
	};
	const loaded = result.loaded || {};
	const failed = result.failed || {};
	const lines = Object.entries(loaded).map(
		([kind, count]) => `<li>${labels[kind] || kind}: ${__("{0} loaded", [count])}</li>`
	);
	const errors = Object.entries(failed).map(
		([kind, message]) =>
			`<li>${labels[kind] || kind}: ${__("not loaded")} – <span class="text-muted">${frappe.utils.escape_html(
				message
			)}</span></li>`
	);
	let message = `<ul>${lines.join("")}${errors.join("")}</ul>`;
	if (Object.keys(loaded).length) {
		message += `<p>${__(
			"Now pick the statuses and the carrier by name in the Synchronisation tab. The shop IDs are stored as before."
		)}</p>`;
	}
	if (errors.length) {
		message += `<p>${__(
			"Lists that could not be loaded keep their previous values; you can still type the IDs by hand."
		)}</p>`;
	}
	frappe.msgprint({
		title: __("Shop Data"),
		message: message,
		indicator: !errors.length ? "green" : lines.length ? "orange" : "red",
	});
}

function load_shop_data(frm) {
	const run = () => {
		if (frm.is_new()) return;
		frappe.call({
			method: API + "load_shop_data",
			type: "POST",
			args: { shop: frm.doc.name },
			freeze: true,
			freeze_message: __("Loading shop data …"),
			callback: (r) => frm.reload_doc().then(() => show_load_result(r.message || {})),
		});
	};
	if (frm.is_new() || frm.is_dirty()) {
		frm.save().then(run);
	} else {
		run();
	}
}

function choose_import_statuses(frm) {
	const statuses = shop_options(frm, "Order Status");
	const current = split_ids(frm.doc.import_status_ids);
	const unknown = current
		.filter((id) => !label_of(statuses, id))
		.map((id) => ({ value: id, label: __("ID {0} (not in the shop)", [esc(id)]) }));
	const dialog = new frappe.ui.Dialog({
		title: __("Choose Import Statuses"),
		fields: [
			{
				fieldtype: "MultiCheck",
				fieldname: "statuses",
				label: __("Orders in these statuses are imported"),
				columns: 1,
				options: [...statuses, ...unknown].map((o) => ({
					label: o.label,
					value: o.value,
					checked: current.includes(o.value),
				})),
			},
			{
				fieldtype: "HTML",
				fieldname: "shipped_note",
				options: `<p class="text-muted small">${__(
					"Orders in the shipped status are always imported as well."
				)}</p>`,
			},
		],
		primary_action_label: __("Apply"),
		primary_action(values) {
			const chosen = (values.statuses || []).slice().sort((a, b) => Number(a) - Number(b));
			if (!chosen.length) {
				frappe.msgprint(__("Choose at least one status."));
				return;
			}
			frm.set_value("import_status_ids", chosen.join(","));
			dialog.hide();
		},
	});
	dialog.show();
}

// -- take over missing items ---------------------------------------------------------------------
function item_link(code) {
	return `<a href="/desk/item/${encodeURIComponent(code)}">${esc(code)}</a>`;
}

function show_take_over_result(r) {
	if (r.queued !== undefined) {
		frappe.msgprint({
			title: __("Take Over Missing Items"),
			indicator: r.queued ? "green" : "orange",
			message: r.queued
				? __(
						"{0} items are being created in the background. The results appear in the Sync Log (operation Item).",
						[r.count]
				  )
				: __("A take-over is already running for this shop."),
		});
		return;
	}
	const created = r.created || [];
	const existing = r.existing || [];
	const failed = Object.entries(r.failed || {});
	let html = "";
	if (created.length) {
		html += `<p><b>${__("Created")} (${created.length})</b></p><ul>${created
			.map((c) => `<li>${item_link(c.item_code)}</li>`)
			.join("")}</ul>`;
	}
	if (existing.length) {
		html += `<p><b>${__("Already existed")} (${existing.length})</b></p><ul>${existing
			.map((c) => `<li>${item_link(c.item_code)}</li>`)
			.join("")}</ul>`;
	}
	if (failed.length) {
		html += `<p><b>${__("Failed")} (${failed.length})</b></p><ul>${failed
			.map(([id, msg]) => `<li>${__("Shop product {0}", [esc(id)])}: ${esc(msg)}</li>`)
			.join("")}</ul>`;
	}
	frappe.msgprint({
		title: __("Take Over Missing Items"),
		indicator: failed.length ? (created.length ? "orange" : "red") : "green",
		message: html,
	});
}

function take_over_dialog(frm, products) {
	const rows = products
		.map(
			(p) =>
				`<tr><td><input type="checkbox" class="take-over-check" value="${esc(p.product_id)}"></td>
				<td>${esc(p.model)}</td><td>${esc(p.name)}</td><td>${esc(p.ean)}</td>
				<td class="text-right">${p.price === null || p.price === undefined ? "" : format_currency(p.price, "EUR")}</td></tr>`
		)
		.join("");
	const dialog = new frappe.ui.Dialog({
		title: __("Take Over Missing Items"),
		size: "extra-large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "products",
				options: `<p class="text-muted small">${__(
					"These shop products have no Item in ERPNext. Tick the ones to create. No opening stock is booked."
				)}</p>
				<p><button type="button" class="btn btn-xs btn-default take-over-all">${__("Select all")}</button></p>
				<div style="max-height:50vh;overflow:auto"><table class="table table-bordered table-sm">
				<thead><tr><th></th><th>${__("Item Number")}</th><th>${__("Name")}</th><th>${__("EAN")}</th>
				<th class="text-right">${__("Price")}</th></tr></thead><tbody>${rows}</tbody></table></div>`,
			},
		],
		primary_action_label: __("Take Over"),
		primary_action() {
			const ids = dialog.$wrapper
				.find(".take-over-check:checked")
				.map((_i, el) => el.value)
				.get();
			if (!ids.length) {
				frappe.msgprint(__("Choose at least one product."));
				return;
			}
			dialog.hide();
			frappe.call({
				method: API + "take_over_items",
				type: "POST",
				args: { shop: frm.doc.name, product_ids: ids },
				freeze: true,
				freeze_message: __("Creating items …"),
				callback: (r) => show_take_over_result(r.message || {}),
			});
		},
	});
	dialog.$wrapper.on("click", ".take-over-all", () => {
		const boxes = dialog.$wrapper.find(".take-over-check");
		const all = boxes.length === boxes.filter(":checked").length;
		boxes.prop("checked", !all);
	});
	dialog.show();
}

function take_over_missing_items(frm) {
	frappe.call({
		method: API + "get_missing_products",
		args: { shop: frm.doc.name },
		freeze: true,
		freeze_message: __("Loading shop products …"),
		callback: (r) => {
			const products = r.message || [];
			if (!products.length) {
				frappe.msgprint({
					title: __("Take Over Missing Items"),
					message: __("All shop products exist in ERPNext"),
					indicator: "green",
				});
				return;
			}
			take_over_dialog(frm, products);
		},
	});
}

frappe.ui.form.on("Modified Shop", {
	setup(frm) {
		frm.set_query("warehouse", () => {
			const filters = { is_group: 0 };
			if (frm.doc.company) filters.company = frm.doc.company;
			return { filters };
		});
	},

	load_shop_data_button(frm) {
		load_shop_data(frm);
	},

	choose_import_statuses(frm) {
		choose_import_statuses(frm);
	},

	import_status_ids(frm) {
		apply_shop_options(frm);
	},

	cancelled_status_id(frm) {
		apply_shop_options(frm);
	},

	shipped_status_id(frm) {
		apply_shop_options(frm);
	},

	dhl_carrier_id(frm) {
		apply_shop_options(frm);
	},

	refresh(frm) {
		set_autocomplete(frm, "api_password", "new-password");
		frm.trigger("set_status_intro");
		apply_shop_options(frm);
		if (frm.is_new()) return;
		const group = __("Actions");
		frm.add_custom_button(__("Load Shop Data"), () => load_shop_data(frm), group);
		frm.add_custom_button(__("Take Over Missing Items"), () => take_over_missing_items(frm), group);
		frm.add_custom_button(
			__("Test Connection"),
			() => {
				frappe.call({
					method: API + "test_connection",
					args: { shop: frm.doc.name },
					freeze: true,
					callback: (r) => {
						frappe.msgprint({
							title: __("Connection OK"),
							message: `<pre>${frappe.utils.escape_html(JSON.stringify(r.message, null, 2))}</pre>`,
							indicator: "green",
						});
					},
				});
			},
			group
		);
		frm.add_custom_button(
			__("Import Orders Now"),
			() =>
				queue_action(
					frm,
					"import_now",
					__("Import started — results appear in the Sync Log and as Sales Invoices"),
					__("Import already running")
				),
			group
		);
		if (frm.doc.sync_stock) {
			frm.add_custom_button(
				__("Sync Stock Now"),
				() =>
					queue_action(
						frm,
						"sync_stock_now",
						__("Stock sync started"),
						__("Stock sync already running")
					),
				group
			);
		}
	},

	set_status_intro(frm) {
		const guide = `<a href="/modified-shop-guide">${__("Setup guide")}</a>`;
		if (frm.is_new() || !frm.doc.enabled) {
			frm.set_intro(
				__("Fill in the tabs, test the connection, then enable the shop.") + " " + guide,
				"blue"
			);
			return;
		}
		frappe.call({
			method: API + "get_status",
			args: { shop: frm.doc.name },
			callback: (r) => {
				const s = r.message || {};
				const last = s.last_import ? frappe.datetime.prettyDate(s.last_import) : __("never");
				const name = encodeURIComponent(frm.doc.name);
				const parts = [
					__("Last import: {0}", [last]),
					__("{0} imported in the last 7 days", [s.imported_7d || 0]),
					__("{0} waiting for import status", [s.waiting || 0]),
					__("{0} import errors", [s.errors || 0]),
				];
				const links = [
					`<a href="/desk/modified-sync-log?shop=${name}">${__("Sync Log")}</a>`,
					`<a href="/desk/sales-invoice?modified_shop=${name}">${__("Sales Invoices")}</a>`,
					guide,
				];
				if (s.integration_log) {
					links.splice(
						2,
						0,
						`<a href="/desk/integration-log-entry?source=Modified%20Shop">${__("Integration Log")}</a>`
					);
				}
				frm.set_intro(
					`${frappe.utils.escape_html(parts.join(" · "))}<br>${links.join(" · ")}`,
					s.errors ? "red" : "green"
				);
			},
		});
	},
});
