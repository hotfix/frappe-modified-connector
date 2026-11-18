frappe.ui.form.on("Modified Sync Log", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.operation !== "Import" || frm.doc.status !== "Error") return;
		if (frm.doc.ref_doctype === "Sales Invoice" && frm.doc.ref_name) return;
		frm.add_custom_button(__("Retry"), () => {
			frappe.call({
				method: "modified_connector.modified_connector.doctype.modified_sync_log.modified_sync_log.retry",
				args: { log: frm.doc.name },
				freeze: true,
				callback: (r) => {
					if (r.message) {
						frappe.show_alert({ message: __("Imported as {0}", [r.message]), indicator: "green" });
					} else {
						frappe.show_alert({ message: __("Not imported, see the log for the reason"), indicator: "orange" });
					}
					frm.reload_doc();
				},
			});
		});
	},
});
