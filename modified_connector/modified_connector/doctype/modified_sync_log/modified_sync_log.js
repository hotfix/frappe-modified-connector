frappe.ui.form.on("Modified Sync Log", {
	refresh(frm) {
		// failed imports and orders waiting for an import status, not run-level logs (decided on the server)
		if (frm.is_new() || !(frm.doc.__onload && frm.doc.__onload.can_retry)) return;
		frm.add_custom_button(__("Retry"), () => {
			frappe.call({
				method: "modified_connector.modified_connector.doctype.modified_sync_log.modified_sync_log.retry",
				args: { log: frm.doc.name },
				freeze: true,
				callback: (r) => {
					if (r.message) {
						frappe.show_alert({ message: __("Order is imported as {0}", [r.message]), indicator: "green" });
					}
					frm.reload_doc();
				},
			});
		});
	},
});
