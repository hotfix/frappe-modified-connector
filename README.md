# Modified Connector

Frappe/ERPNext v16 app: connector for modified eCommerce shops (order import, stock sync).

Work in progress.

## Integration hooks

Labels are not created by this app. A label app (e.g. `dhl_connector`) announces new labels through the
hook `on_shipping_label_created`; any channel app can implement it. For shop orders this app sends the
tracking number to the shop and sets the order to "shipped".

A label app calls, after storing the label:

```python
for method in frappe.get_hooks("on_shipping_label_created"):
	frappe.get_attr(method)(doctype="Sales Invoice", name=invoice, carrier="dhl", tracking_number=number)
```

`carrier` is a lowercase string (`"dhl"`). Implementations ignore documents they do not own. Labels made
by hand can be sent with the "Send Tracking Number" button (Modified menu) on the Sales Invoice.

License: GPLv3
