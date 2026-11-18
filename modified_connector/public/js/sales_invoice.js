frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || !frm.doc.modified_order_id) return;
		frm.add_custom_button(
			__("Send Tracking Number"),
			() => {
				frappe.prompt(
					{ fieldname: "tracking_number", fieldtype: "Data", label: __("Tracking Number"), reqd: 1 },
					(values) => {
						frappe.call({
							method: "modified_connector.sync.shipping.send_tracking",
							args: { invoice: frm.doc.name, tracking_number: values.tracking_number },
							freeze: true,
							callback: () => {
								frappe.show_alert({ message: __("Tracking number sent"), indicator: "green" });
								frm.reload_doc();
							},
						});
					},
					__("Send Tracking Number"),
					__("Send")
				);
			},
			__("Modified")
		);
	},
});
