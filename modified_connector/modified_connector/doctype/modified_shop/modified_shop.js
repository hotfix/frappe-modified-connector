frappe.ui.form.on("Modified Shop", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Test Connection"), () => {
			frappe.call({
				method: "modified_connector.modified_connector.doctype.modified_shop.modified_shop.test_connection",
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
		});
	},
});
