# Modified Connector

A Frappe/ERPNext v16 app that connects ERPNext to a shop running [modified eCommerce](https://www.modified-shop.org/)
through the shop's REST API (`/api/v1`).
This is an independent community project and not an official module of the modified eCommerce project.

- **Order import:** shop orders become Sales Invoices (customer, contact and addresses are created or reused).
- **Stock sync:** ERPNext stock levels are pushed to the shop.
- **Tracking:** tracking numbers are sent back to the shop, a history entry is added (customer notified) and the order status is set to "shipped".
- **Shop data:** order statuses, carriers, payment classes and tax rates are loaded from the shop
  (**Load Shop Data**) and picked by name instead of by id. Orders in the shipped status are always imported.
- **Items from the shop:** shop products without an Item can be taken over from a dialog (**Take Over Missing
  Items**) or created during the order import (*Create Missing Items on Import*): name, EAN barcode, Item Tax
  Template from the Tax Map and the gross price in the default selling price list. No opening stock is booked.
- **Customers:** the Territory of a new customer follows the billing country (a Territory named like the
  country, else the default territory).
- **Sync log:** import errors and orders waiting for an import status are logged; such an order can be
  imported again from its log entry with **Retry** once the cause is fixed.

## Limits of version 1

Orders outside these limits are not booked. They are logged in the *Modified Sync Log* as errors with the
reason. Later runs fetch them again only within the retry window (see *Retry Window (Days)*); after that the
log entry is what is left, and the order can still be imported from there with **Retry**.

- Currency EUR only.
- B2C orders with gross prices (prices including VAT) only. Orders with net prices are rejected.
- Simple products only. Products with attributes or variants are rejected.
- One warehouse per shop.
- Tax rates are mapped explicitly (see *Tax Map*); an order with an unmapped rate is rejected.
- Redeemed gift vouchers (`ot_gv`) are not supported. A gift voucher is a payment (multi-purpose voucher),
  not a price reduction, so such an order is rejected and has to be booked by hand.
- Orders cancelled in the shop after they were imported are only reported in the log. Credit notes are not
  created automatically.

## Installation

```bash
bench get-app https://github.com/hotfix/frappe-modified-connector
bench --site <site> install-app modified_connector
```

The app requires ERPNext. Installing it adds custom fields (`modified_shop`, `modified_order_id`,
`modified_payment_class`, `modified_tracking_number`) to the Sales Invoice and two fields to the Customer.
Uninstalling the app removes these custom fields again.

## Configuration

A step-by-step setup guide in German (prerequisites, every field with example values, first run,
troubleshooting) is in [`modified_connector/docs/setup-guide-de.md`](modified_connector/docs/setup-guide-de.md).
Logged-in users can read it on the site at `/modified-shop-guide` (also linked from the *modified Shop*
sidebar and the shop form).

Create a *Modified Shop* (search "Modified Shop" in the awesome bar). Use **Test Connection** after saving to
check the credentials.

| Field | Meaning |
| --- | --- |
| Shop Name | Name of the record. |
| Enabled | The scheduler imports orders (every 5 minutes) and syncs stock only for enabled shops. |
| Base URL | Shop URL without `/api/v1`. Must use `https://` (`http://` is only accepted for `localhost`). |
| API Username / API Password | Credentials of the shop API user (the password is stored encrypted). |
| Import From | Orders purchased before this time are never imported. Set it to the go-live time. Mandatory, so the first run never fetches the whole order history. |
| Last Import | Set by the app: purchase time of the newest order seen. |
| Retry Window (Days) | Every run fetches the orders of the last N days before *Last Import* again. This retries orders that failed or that were not yet in an import status when first seen. Older orders are no longer fetched; their log entry stays and can be retried by hand. Default 14. |
| Company, Warehouse | Company of the invoices, and the warehouse that is delivered from and whose stock is pushed. |
| Customer Group, Territory | Used for customers created by the import (non-group records). |
| Invoice Naming Series | Optional naming series for the imported invoices. |
| Item Match Shop Field / Item Match ERP Field | How shop products are matched to items: shop `products_model` or `products_ean` against the ERPNext Item Code or Barcode. Default: `products_model` = Item Code. |
| Shipping Item | Non-stock item used for the shipping rows. **It must list the Item Tax Template of every rate in the Tax Map.** Shipping is split over the tax rates (see below), and ERPNext silently replaces a row template that is not listed on the item. The import detects this and rejects the order instead of booking wrong VAT. |
| Sales Taxes Template | Template with one tax row per VAT rate (separate accounts for 19 % and 7 %, as in SKR03). Rows are treated as included in the price. |
| Tax Map | Tax rate (%) of a shop order line to the *Item Tax Template* used for that line. **Every item must list the Item Tax Template of its shop rate**, directly or through its Item Group. ERPNext replaces a template that is not listed with one that is; with a 19 % template on the root Item Group, every 7 % item therefore needs the 7 % template on the item (or its group). The import rejects an order where this happened instead of booking wrong VAT. |
| Payment Map | Per shop payment class (e.g. `paypal`, `moneyorder`): *Mode of Payment* and *Paid at Checkout*. |
| Import Status IDs | Comma separated shop order status ids that are imported (mandatory, default `1,2`). An order in another status is skipped and logged once as "Waiting for import status"; it is imported when it reaches an import status within the retry window, or later with **Retry** on that log entry. |
| Cancelled Status ID | Order status id for cancelled orders (default 4). Never imported. |
| Shipped Status ID | Status id set when a tracking number is sent (default 3). |
| DHL Carrier ID | Carrier id of DHL in the shop's tracking table (default 1). |
| Submit Invoices | Off: imported invoices stay drafts for review. On: they are submitted, which triggers payment and delivery. |
| Sync Stock | Push stock changes of the warehouse to the shop. Enable it only after the opening stock in ERPNext is correct: the shop quantity is overwritten. Orders that are not yet imported are not accounted for. |

### Paid at Checkout

For payment classes with *Paid at Checkout* checked (e.g. PayPal, where the shop collects the money at
checkout), submitting the invoice creates a Payment Entry in the mapped *Mode of Payment*, dated on the order
date. Leave it unchecked for methods that are paid later, such as prepayment by bank transfer: the invoice
stays open until you reconcile the bank transaction. A payment class that is not in the map creates no
payment.

## How it works

**Mapping.** Shop prices are gross. Each order line becomes an invoice row with the gross unit price and the
Item Tax Template of its tax rate; the tax rows are "included in print rate". The item of a line is found with
the item match fields. A missing or disabled item rejects the order.

**Totals.** After the invoice is built, its grand total must equal the shop's order total within one cent.
Otherwise the order is not booked and the difference is logged. Rounded totals are disabled.

**Shipping split.** The shipping cost is a gross amount that is taxed like the goods. It is split over the tax
rates in proportion to the gross value of the lines, with one shipping row per rate, so a mixed 19 % / 7 %
order gets the correct VAT on shipping.

**Coupons and discounts.** Coupon and discount totals of the order are booked as a discount on the grand
total. A redeemed gift voucher rejects the order (see *Limits*). An unknown total class rejects the order,
because the grand total could no longer be checked.

**Dropshipping.** Items flagged *Delivered by Supplier* in ERPNext are marked as drop-shipped on the invoice
row. An order that contains such an item does not update stock with the invoice. On submit a Delivery Note is
created for its stock items; drop-shipped rows get none. Other orders deduct stock with the invoice
(*Update Stock*).

**Stock.** The pushed quantity is actual minus reserved quantity of the shop warehouse, minus the quantity in
draft shop invoices that will deduct stock from it (with *Submit Invoices* off, imported invoices have no stock
entry yet), never negative. Orders that are not yet imported are not accounted for. Stock Ledger Entries
trigger a background job per item and shop; the job pushes again (up to three times) if the quantity changed
while it was pushing, and a daily full sync repairs drift. Items
that are not stock items, are disabled or are drop-shipped are not pushed. Items that do not exist in the shop
are logged as skipped.

**Customers and addresses.** Account customers are found by their shop customer id, guests by email. An
address is reused when recipient (company and name), street, postcode and city match; a delivery to another
recipient, even at the billing street, gets its own shipping address.

**Idempotency.** An order is imported once: shop and order id are checked under a database lock, and every
order is committed on its own, so concurrent runs and restarts are safe.

**Errors and retry.** Failed orders are written to the *Modified Sync Log* (once per distinct message) and
fetched again by later runs within the retry window. Orders not yet in an import status get a "Skipped" entry
"Waiting for import status"; when such an order is imported, the entry points to its invoice. After fixing the
cause (for example creating the missing item) open the log entry and click **Retry** to import the order
immediately, also when it is older than the retry window. The old log entry is then marked
"Skipped" with a pointer to the new one. If the order meanwhile was imported by the scheduler, was cancelled in
the shop, is no longer in an import status or lies before *Import From*, the entry is closed as "Skipped" with
that reason and you are told so. A waiting order that is still not in an import status stays open. An aborted
run (shop not reachable) is logged with the reference "(run)" and cannot be retried; the next run covers it.
Retry requires permission to create Sales Invoices and to edit the log entry.

**Cancel and amend.** An amended shop invoice keeps the order id. Submitting it creates no second Payment Entry
(also not when a submitted payment with the order id as reference already exists for the customer) and no
second Delivery Note. Instead a "Skipped" log entry and a message tell you to allocate the existing payment with
*Payment Reconciliation* and to check the deliveries of the original invoice.

## Navigation

On the home screen the app appears as the tile *modified Shop* (hook `add_to_apps_screen`, shown to users
who can read Modified Shop). Sites in the "Desktop Icons" desktop mode get the shipped Desktop Icon instead.
The workspace *modified Shops* (route `/desk/modified-shops`, so it does not collide with the list route of the DocType Modified Shop) and its sidebar link to the shops, the sync log and the results
(invoices, payments, delivery notes, customers). The tile logo is served from `assets/modified_connector`,
which `bench build` creates for apps present in the bench where the assets are served (a plain restart
does not recreate the symlink); run `bench --site <site> migrate` and `bench build` after installing or updating.

## Integration hooks

Labels are not created by this app. A label app (e.g. `dhl_connector`) announces new labels through the
hook `on_shipping_label_created`; any channel app can implement it. For shop orders this app sends the
tracking number to the shop, adds the history entry and sets the order status to "shipped" (the history entry alone would not change the status).

A label app calls, after storing the label:

```python
for method in frappe.get_hooks("on_shipping_label_created"):
	frappe.get_attr(method)(doctype="Sales Invoice", name=invoice, carrier="dhl", tracking_number=number)
```

`carrier` is a lowercase string (`"dhl"`). Implementations ignore documents they do not own. Labels made
by hand can be sent with the "Send Tracking Number" button (Modified menu) on the Sales Invoice.

## Integration Log

If the app [integration_log](https://github.com/hotfix/frappe-integration-log) is installed on the site,
the Modified Sync Log is registered with it (hook `integration_logs`). Nothing changes without it; this
app has no dependency on it.

- The shop is shown as the area of each entry (hook key `area_field`).
- Logs with status **Error** are mirrored as errors.
- Logs of orders that wait for an import status (status "Skipped", message "Waiting for import status",
  no invoice yet) are mirrored as warnings. Other "Skipped" and "Success" logs are not mirrored.
- Group: shop, operation and reference. A later log for the same order that is not an error or warning
  (for example a successful import), or a retry that closes the log, resolves the open entries
  automatically.
- Retry is offered in the Integration Log for rows that can be retried here (failed or waiting order
  imports).

## Security notes

- Create a dedicated API user in the shop with the minimum rights this app needs: read orders and products,
  update product stock, add order tracking and status history. Do not use an administrator account.
- The shop URL must use HTTPS (plain HTTP is only accepted for `localhost`), because the API password is sent
  with every request.
- The API password is stored in a Password field, encrypted with the site's `encryption_key`. Back up the
  site config together with the database.
- *Test Connection* is limited to the System Manager role, and so is access to the sync log.

## Translations

German translations are included (`modified_connector/locale/de.po`). To refresh them after changing strings:
`bench --site <site> generate-pot-file --app modified_connector`, then `update-po-files` and
`compile-po-to-mo`.

## Support

Provided as is, with no guaranteed support. Issues and pull requests are welcome but may not be answered.

License: GPLv3
