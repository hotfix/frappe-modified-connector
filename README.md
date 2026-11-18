# Modified Connector

A Frappe/ERPNext v16 app that connects ERPNext to a shop running [modified eCommerce](https://www.modified-shop.org/)
through the shop's REST API (`/api/v1`).

- **Order import:** shop orders become Sales Invoices (customer, contact and addresses are created or reused).
- **Stock sync:** ERPNext stock levels are pushed to the shop.
- **Tracking:** tracking numbers are sent back to the shop and the order is set to "shipped".
- **Sync log:** every import error is logged and can be retried with one click.

## Limits of version 1

Orders outside these limits are not booked. They are logged in the *Modified Sync Log* as errors with the
reason, so nothing disappears silently.

- Currency EUR only.
- B2C orders with gross prices (prices including VAT) only. Orders with net prices are rejected.
- Simple products only. Products with attributes or variants are rejected.
- One warehouse per shop.
- Tax rates are mapped explicitly (see *Tax Map*); an order with an unmapped rate is rejected.
- Orders cancelled in the shop after they were imported are only reported in the log. Credit notes are not
  created automatically.

## Installation

```bash
bench get-app https://github.com/hotfix/frappe-modified-connector
bench --site <site> install-app modified_connector
```

The app requires ERPNext. Installing it adds custom fields (`modified_shop`, `modified_order_id`,
`modified_payment_class`, `modified_tracking_number`) to the Sales Invoice and two fields to the Customer.

## Configuration

Create a *Modified Shop* (search "Modified Shop" in the awesome bar). Use **Test Connection** after saving to
check the credentials.

| Field | Meaning |
| --- | --- |
| Shop Name | Name of the record. |
| Enabled | The scheduler imports orders (every 5 minutes) and syncs stock only for enabled shops. |
| Base URL | Shop URL without `/api/v1`. |
| API Username / API Password | Credentials of the shop API user (the password is stored encrypted). |
| Import From | Orders purchased before this time are never imported. Set it to the go-live time. |
| Last Import | Set by the app: purchase time of the newest order seen. |
| Retry Window (Days) | Every run fetches the orders of the last N days before *Last Import* again. This retries orders that failed or that were not yet in an import status when first seen. Default 14. |
| Company, Warehouse | Company of the invoices, and the warehouse that is delivered from and whose stock is pushed. |
| Customer Group, Territory | Used for customers created by the import (non-group records). |
| Invoice Naming Series | Optional naming series for the imported invoices. |
| Item Match Shop Field / Item Match ERP Field | How shop products are matched to items: shop `products_model` or `products_ean` against the ERPNext Item Code or Barcode. Default: `products_model` = Item Code. |
| Shipping Item | Non-stock item used for the shipping rows. **It must list the Item Tax Template of every rate in the Tax Map.** Shipping is split over the tax rates (see below), and ERPNext silently replaces a row template that is not listed on the item. The import detects this and rejects the order instead of booking wrong VAT. |
| Sales Taxes Template | Template with one tax row per VAT rate (separate accounts for 19 % and 7 %, as in SKR03). Rows are treated as included in the price. |
| Tax Map | Tax rate (%) of a shop order line to the *Item Tax Template* used for that line. |
| Payment Map | Per shop payment class (e.g. `paypal`, `moneyorder`): *Mode of Payment* and *Paid at Checkout*. |
| Import Status IDs | Comma separated shop order status ids that are imported (default `1,2`). Orders in other statuses are left until they reach one. |
| Cancelled Status ID | Order status id for cancelled orders (default 4). Never imported. |
| Shipped Status ID | Status id set when a tracking number is sent (default 3). |
| DHL Carrier ID | Carrier id of DHL in the shop's tracking table (default 1). |
| Submit Invoices | Off: imported invoices stay drafts for review. On: they are submitted, which triggers payment and delivery. |
| Sync Stock | Push stock changes of the warehouse to the shop. |

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

**Coupons and discounts.** Coupon, discount and gift voucher totals of the order are booked as a discount on
the grand total. An unknown total class rejects the order, because the grand total could no longer be checked.

**Dropshipping.** Items flagged *Delivered by Supplier* in ERPNext are marked as drop-shipped on the invoice
row. An order that contains such an item does not update stock with the invoice. On submit a Delivery Note is
created for its stock items; drop-shipped rows get none. Other orders deduct stock with the invoice
(*Update Stock*).

**Stock.** The pushed quantity is actual minus reserved quantity of the shop warehouse, never negative.
Stock Ledger Entries trigger a background job per item and shop, and a daily full sync repairs drift. Items
that are not stock items, are disabled or are drop-shipped are not pushed. Items that do not exist in the shop
are logged as skipped.

**Idempotency.** An order is imported once: shop and order id are checked under a database lock, and every
order is committed on its own, so concurrent runs and restarts are safe.

**Errors and retry.** Failed orders are written to the *Modified Sync Log* (once per distinct message) and
fetched again by later runs within the retry window. After fixing the cause (for example creating the missing
item) open the log entry and click **Retry** to import the order immediately. The old log entry is then marked
"Skipped" with a pointer to the new one. If the order meanwhile was imported by the scheduler, was cancelled in
the shop, is no longer in an import status or lies before *Import From*, the entry is closed as "Skipped" with
that reason and you are told so. Retry requires permission to create Sales Invoices.

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

## Security notes

- Create a dedicated API user in the shop with the minimum rights this app needs: read orders and products,
  update product stock, add order tracking and status history. Do not use an administrator account.
- Use HTTPS for the shop URL.
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
