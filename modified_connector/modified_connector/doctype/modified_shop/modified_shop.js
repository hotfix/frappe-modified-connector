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

// Keep password managers and browsers from filling the shop API login with the ERPNext login.
function set_autocomplete(frm, fieldname, value) {
	const field = frm.get_field(fieldname);
	if (field && field.$input) field.$input.attr("autocomplete", value);
}

frappe.ui.form.on("Modified Shop", {
	refresh(frm) {
		set_autocomplete(frm, "api_username", "off");
		set_autocomplete(frm, "api_password", "new-password");
		frm.trigger("set_status_intro");
		if (frm.is_new()) return;
		const group = __("Actions");
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
