# Einrichtung der Shop-Anbindung (modified eCommerce)

Diese Anleitung führt Schritt für Schritt durch die Einrichtung der App **Modified Connector**. Sie verbindet
ERPNext mit einem Shop, der mit modified eCommerce betrieben wird, und holt die Bestellungen über die REST-API
des Shops (`/api/v1`) ab. Alle Beispielwerte (Shop-Adresse, Firmenname, Kontonamen) sind Platzhalter, die Sie
durch Ihre eigenen Werte ersetzen.

Sie finden die Anbindung auf der Startseite von ERPNext als Kachel **Shop-Anbindung**. Die Seitenleiste dort
enthält: **Modified-Shop** (die Einstellungen), **Modified-Synchronisationsprotokoll**, unter *Ergebnisse*
die **Ausgangsrechnungen**, **Zahlungen**, **Lieferscheine** und **Kunden** sowie unter *Hilfe* diese
Einrichtungsanleitung.

## 1. Was die Anbindung macht

**Bestellimport (alle 5 Minuten).** Für jeden aktivierten Shop holt ERPNext alle 5 Minuten die neuen
Bestellungen ab. Importiert werden nur Bestellungen, deren Bestellstatus in den *Status-IDs für den Import*
steht (z. B. Offen und In Bearbeitung) und die nicht vor dem Zeitpunkt *Import ab* aufgegeben wurden.
Bestellungen im Status *Status-ID Versendet* werden **immer** importiert, auch wenn dieser Status nicht in der
Liste steht. So geht eine Bestellung nicht verloren, die im Shop schon als versendet markiert wurde, bevor der
erste Import lief.
Jede Bestellung wird genau einmal importiert, auch wenn sie bei späteren Läufen erneut abgerufen wird. Für jede
importierte Bestellung entsteht:

- ein **Kunde**: Kunden mit Kundenkonto im Shop werden über ihre Shop-Kundennummer wiedererkannt, Gastbesteller
  über ihre E-Mail-Adresse. Ist der Kunde neu, wird er mit der eingestellten *Kundengruppe* angelegt. Sein Gebiet richtet
  sich nach dem Land der Rechnungsadresse (siehe Abschnitt 2, „Gebiete je Land“); gibt es dafür kein passendes
  Gebiet, bekommt er das *Standard-Gebiet*. Dazu ein Kontakt mit der E-Mail-Adresse und die Rechnungsadresse. Weicht die Lieferadresse ab
  (anderer Empfänger oder andere Anschrift), bekommt sie eine eigene Lieferadresse.
- eine **Ausgangsrechnung** mit dem Bestelldatum als Buchungsdatum. Die Positionen werden mit den
  Bruttopreisen aus dem Shop übernommen, die Versandkosten als Position(en) mit dem *Versandartikel*, Gutscheine
  und Rabatte als Rabatt auf den Gesamtbetrag. Die Bestellnummer des Shops steht im Feld *Modified Order ID*,
  der Code der Zahlungsart im Feld *Modified Payment Class*. Ist *Rechnungen buchen* aus, bleibt die Rechnung
  ein **Entwurf** zur Prüfung; ist es an, wird sie sofort **gebucht**.
- Beim **Buchen** der Rechnung (automatisch oder später von Hand mit „Buchen“):
  - eine **Zahlung** (Zahlungseingang), wenn die Zahlungsart der Bestellung in der *Zahlungszuordnung* steht
    und dort *Bei Bestellung bezahlt* angehakt ist (z. B. PayPal). Die Zahlung bekommt das Bestelldatum und
    die Bestellnummer als Referenz. Bei Vorkasse per Überweisung bleibt die Rechnung offen, bis Sie den
    Bankeingang zuordnen.
  - der **Lagerabgang**: Normalerweise bucht die Rechnung selbst den Bestand aus dem *Lager* aus
    (*Lagerbestand aktualisieren* ist gesetzt). Enthält die Bestellung einen Artikel, der vom Lieferanten
    direkt verschickt wird (Streckengeschäft), bucht die Rechnung keinen Bestand; stattdessen wird beim Buchen
    ein **Lieferschein** für die Lagerartikel der Bestellung erstellt. Streckengeschäfts-Positionen bekommen
    keinen Lieferschein.

**Bestandsabgleich.** Ist *Bestand synchronisieren* eingeschaltet, meldet ERPNext dem Shop den verkaufbaren
Bestand aus dem eingestellten *Lager*: nach jeder Lagerbewegung des betroffenen Artikels und zusätzlich einmal
täglich für alle Artikel. Gemeldet wird: Ist-Bestand minus reservierte Menge minus die Menge in noch nicht
gebuchten Shop-Rechnungen (Entwürfe), nie weniger als 0. Bestellungen, die noch nicht importiert sind, sind
darin nicht berücksichtigt. Nicht gemeldet werden Artikel ohne Lagerführung, deaktivierte Artikel und
Streckengeschäfts-Artikel.

**Sendungsnummer zurück an den Shop.** Bei einer gebuchten Shop-Rechnung gibt es im Menü **Modified** die
Schaltfläche **Sendungsnummer senden** (oder eine Versand-App erledigt das automatisch, sobald sie ein Etikett
erzeugt). Die Sendungsnummer wird im Shop mit dem Versanddienstleister DHL gespeichert. Ist die Bestellung
noch in einem Import-Status, bekommt sie außerdem einen Eintrag im Bestellverlauf (markiert als „Kunde
benachrichtigt“) und die *Status-ID Versendet*. Unterstützt wird derzeit nur DHL.

**Grenzen dieser Version.** Folgende Bestellungen werden **nicht** gebucht, sondern als Fehler im
Synchronisationsprotokoll gemeldet: andere Währung als EUR, Nettopreise (B2B), Artikel mit Attributen bzw.
Varianten, eingelöste Geschenkgutscheine (`ot_gv`) und unbekannte Summenzeilen. Wird eine bereits importierte
Bestellung im Shop storniert, wird das nur gemeldet; eine Gutschrift erstellen Sie selbst. Pro Shop gibt es
genau ein Lager.

## 2. Voraussetzungen in ERPNext

Legen Sie diese Stammdaten an, **bevor** Sie den Shop einrichten. Jede Liste finden Sie, indem Sie ihren
Namen in die Suchleiste oben eingeben (in Klammern der englische Name, falls Ihre Oberfläche englisch ist).

| Was | Wo (Suchleiste) | Beispiel | Worauf achten |
| --- | --- | --- | --- |
| Unternehmen | Unternehmen (Company) | Meine Firma GmbH, Kürzel MF | Währung EUR. |
| Lager | Lager (Warehouse) | Lagerräume - MF | Ein Lager des Unternehmens, kein Gruppenlager; aus ihm wird versandt und sein Bestand an den Shop gemeldet. |
| Kundengruppe | Kundengruppe (Customer Group) | Einzelperson | Darf keine Gruppe sein. Wurde ERPNext auf Englisch eingerichtet, heißt sie „Individual“. |
| Standard-Gebiet | Gebiet (Territory) | Germany | Keine Gruppe. Es gilt für Kunden, deren Land kein eigenes Gebiet hat (siehe unten „Gebiete je Land“). |
| Umsatzsteuerkonten | Kontenplan (Chart of Accounts) | Umsatzsteuer 19 %, Umsatzsteuer 7 % | Je Steuersatz ein eigenes Konto. |
| Artikelsteuervorlagen | Artikelsteuervorlage (Item Tax Template) | „USt 19 %“ und „USt 7 %“ | Je Steuersatz eine Vorlage (siehe unten). |
| Umsatzsteuervorlage | Vorlage für Verkaufssteuern und -abgaben (Sales Taxes and Charges Template) | „Inland - MF“ | Eine Zeile je Steuersatz (siehe unten). |
| Artikel | Artikel (Item) | Artikel-Code `HOLZ-ZUG-01` | Der Artikel-Code (oder der Barcode) muss genau dem Wert im Shop entsprechen (siehe Reiter „Artikel, Steuern & Zahlungen“). Sie müssen die Artikel nicht von Hand anlegen: **Fehlende Artikel aus Shop übernehmen** erledigt das (Abschnitt 5b). |
| Versandartikel | Artikel (Item) | Artikel-Code `VERSAND`, Name „Versand“ | **Ohne** Haken bei „Lager verwalten“. |
| Zahlungsweisen | Zahlungsweise (Mode of Payment) | „PayPal“, „Überweisung“ | Jede Zeile der Zahlungszuordnung braucht eine Zahlungsweise. Für Zahlungsweisen mit *Bei Bestellung bezahlt* muss zusätzlich in der Kontentabelle der Zahlungsweise für Ihr Unternehmen ein Standardkonto eingetragen sein (z. B. ein Bankkonto „PayPal“). |
| Anfangsbestand | Lagerbuchung (Stock Entry, Typ „Materialannahme“) oder Bestandsabgleich (Stock Reconciliation) | 10 Stück von `HOLZ-ZUG-01` im Lager „Lagerräume - MF“, mit Wertansatz | Jeder Lagerartikel braucht vor dem Buchen der Shop-Rechnungen Bestand im Shop-Lager, denn die Rechnung bucht den Bestand selbst aus. Buchungsdatum des Anfangsbestands **vor** dem ersten Bestelldatum wählen: die Rechnung wird auf das Bestelldatum gebucht. Den *Wertansatz* (Einstandspreis je Stück) angeben. |
| Nummernkreis (optional) | Dokumentenbenennungseinstellungen (Document Naming Settings) | `SHOP-.YYYY.-` | Nur wenn Shop-Rechnungen einen eigenen Nummernkreis bekommen sollen: dort für „Ausgangsrechnung“ eintragen. |

**Gebiete je Land.** Beim Anlegen eines neuen Kunden sucht der Import ein Gebiet (Territory), dessen Name zum Land
der **Rechnungsadresse** der Bestellung passt. Gefunden wird es so: Der Shop liefert den Ländercode (z. B. `DE`);
ERPNext macht daraus den Ländernamen (z. B. „Germany“). Probiert werden dann nacheinander dieser Name, sein
deutscher Name („Deutschland“) und der Name in der Sprache Ihres ERPNext. Das erste vorhandene Gebiet gewinnt.
Gibt es keins, oder ist es eine **Gruppe**, bekommt der Kunde das *Standard-Gebiet*. So legen Sie ein Gebiet
für ein Land an: in der Suchleiste *Gebiet* eingeben, **Neu**, als Namen den Ländernamen eintragen (z. B.
`Deutschland` oder `Germany`, `Österreich` bzw. `Austria`), *Ist Gruppe* **nicht** ankreuzen, als übergeordnetes
Gebiet z. B. „All Territories“ wählen und speichern. Bereits angelegte Kunden werden nicht verändert.

**Steuern richtig einrichten.** Shop-Preise sind Bruttopreise. So passt es zusammen:

1. **Artikelsteuervorlage „USt 19 %“**: in der Tabelle der Steuersätze das Konto Umsatzsteuer 19 % = 19 und dem
   Konto Umsatzsteuer 7 % = 0. **Artikelsteuervorlage „USt 7 %“**: Umsatzsteuer 19 % = 0 und
   Umsatzsteuer 7 % = 7.
2. **Umsatzsteuervorlage „Inland“**: zwei Zeilen, jeweils Typ „Auf Nettosumme“, eine mit dem Konto
   Umsatzsteuer 19 % (Steuersatz 19), eine mit dem Konto Umsatzsteuer 7 % (Steuersatz 7). Den Haken „Ist
   diese Steuer im Basispreis enthalten?“ setzt der Import bei Shop-Rechnungen selbst.
3. **Jeder Artikel** muss in seinem Reiter „Steuer“ die Artikelsteuervorlage seines Steuersatzes
   aufführen, direkt oder über seine Artikelgruppe. ERPNext ersetzt sonst stillschweigend die Vorlage;
   der Import erkennt das und lehnt die Bestellung ab, statt eine falsche Umsatzsteuer zu buchen. Liegt
   „USt 19 %“ auf der obersten Artikelgruppe, braucht also jeder 7-%-Artikel „USt 7 %“ am Artikel (oder an
   seiner Artikelgruppe).
4. **Der Versandartikel** muss **beide** Vorlagen („USt 19 %“ und „USt 7 %“) aufführen. Die Versandkosten
   werden im Verhältnis der Warenwerte auf die Steuersätze aufgeteilt; eine gemischte Bestellung bekommt
   eine Versandzeile je Steuersatz.

**Hintergrundaufträge.** Import und Bestandsabgleich laufen als Hintergrundaufträge. Der Scheduler und die
Hintergrund-Worker Ihrer ERPNext-Installation müssen laufen (bei einer gehosteten Installation ist das in der
Regel der Fall; sonst fragen Sie Ihren Administrator).

## 3. Voraussetzungen im Shop

- Die **REST-API** des Shops muss erreichbar sein (Adresse: Ihre Shop-Adresse plus `/api/v1`, z. B.
  `https://shop.example.com/api/v1`). Ob das klappt, zeigt später die Schaltfläche **Verbindung testen**.
- Legen Sie für ERPNext einen **eigenen API-Benutzer** an (z. B. `api@example.com`), kein
  Administratorkonto. Er braucht nur diese Rechte: Bestellungen und Artikel lesen, den Lagerbestand von
  Artikeln ändern, Sendungsnummern und Einträge im Bestellverlauf hinzufügen und den Bestellstatus ändern.
- Der Shop muss per **HTTPS** erreichbar sein, weil das API-Passwort bei jeder Anmeldung übertragen wird.

## 4. Den Modified-Shop anlegen – Feld für Feld

Öffnen Sie in der Seitenleiste der Shop-Anbindung **Modified-Shop** und legen Sie einen neuen Eintrag an. Das
Formular hat vier Reiter. Lassen Sie beim ersten Speichern *Aktiviert*, *Rechnungen buchen* und *Bestand
synchronisieren* aus.

### Reiter „Verbindung“

| Feld | Beispiel | Bedeutung |
| --- | --- | --- |
| Shop-Name | Hauptshop | Eindeutiger Name dieser Verbindung. Er wird bei jeder importierten Rechnung und jedem Kunden gespeichert; der Eintrag lässt sich später nicht umbenennen. |
| Aktiviert | aus, später an | Nur aktivierte Shops werden alle 5 Minuten importiert und im Bestand abgeglichen. Erst einschalten, wenn der Verbindungstest erfolgreich war. |
| Basis-URL | `https://shop.example.com` | Shop-Adresse **ohne** `/api/v1` (wird es mit eingegeben, entfernt ERPNext es beim Speichern). Muss mit `https://` beginnen; `http://` ist nur für `localhost` erlaubt. |
| API-Benutzername | `api@example.com` | Anmeldename des API-Benutzers aus Abschnitt 3. |
| API-Passwort | – | Passwort des API-Benutzers. Es wird verschlüsselt gespeichert. |
| Import ab | 01.01.2027 00:00 | Bestellungen, die vor diesem Zeitpunkt (Uhrzeit des Shops) aufgegeben wurden, werden nie importiert. Setzen Sie hier Ihren Starttermin, damit nicht die ganze alte Bestellhistorie übernommen wird. Pflichtfeld. |
| Letzter Import | (wird gesetzt) | Bestellzeitpunkt der neuesten Bestellung, die der Import gesehen hat. Nicht editierbar. |
| Wiederholungszeitraum (Tage) | 14 | Jeder Lauf holt die Bestellungen ab *Letzter Import* minus so vielen Tagen erneut ab (frühestens ab *Import ab*). So werden fehlgeschlagene Bestellungen und Bestellungen, die noch nicht im Import-Status waren, automatisch erneut versucht. Mindestens 1. |

### Reiter „ERPNext-Standardwerte“

| Feld | Beispiel | Bedeutung |
| --- | --- | --- |
| Unternehmen | Meine Firma GmbH | Unternehmen der Rechnungen. Die Umsatzsteuervorlage muss zu diesem Unternehmen gehören. |
| Lager | Lagerräume - MF | Aus diesem Lager wird geliefert; sein Bestand wird an den Shop gemeldet. |
| Kundengruppe | Einzelperson | Für neu angelegte Kunden. Darf keine Gruppe sein. |
| Standard-Gebiet | Germany | Für neu angelegte Kunden, deren Land (laut Rechnungsadresse) kein Gebiet mit passendem Namen hat oder dessen Gebiet eine Gruppe ist. Darf selbst keine Gruppe sein. |
| Nummernkreis der Rechnungen | leer oder `SHOP-.YYYY.-` | Optional. Leer: der Standard-Nummernkreis der Ausgangsrechnung (z. B. `ACC-SINV-.YYYY.-`). Ein eigener Nummernkreis muss vorher in den Dokumentenbenennungseinstellungen für die Ausgangsrechnung eingetragen sein. |

### Reiter „Artikel, Steuern & Zahlungen“

Abschnitt *Artikelabgleich* – so findet der Import zu einer Shop-Position den ERPNext-Artikel:

| Feld | Beispiel | Bedeutung |
| --- | --- | --- |
| Artikelabgleich: Shop-Feld | `products_model` | `products_model` ist die Artikelnummer (Modell) des Produkts im Shop, `products_ean` die EAN. |
| Artikelabgleich: ERP-Feld | Item Code (Artikel-Code) | Womit der Shop-Wert in ERPNext verglichen wird: Artikel-Code oder Barcode (Tabelle „Strichcodes“ des Artikels). Übliche Paare: `products_model` mit Item Code, `products_ean` mit Barcode. |

Eine Bestellposition ohne passenden (oder mit deaktiviertem) Artikel lässt die ganze Bestellung als Fehler
scheitern – außer *Fehlende Artikel beim Import anlegen* ist aktiviert.

Abschnitt *Artikel aus dem Shop* – Artikel, die es im Shop gibt, aber noch nicht in ERPNext:

| Feld | Beispiel | Bedeutung |
| --- | --- | --- |
| Fehlende Artikel beim Import anlegen | aus | An: fehlt zu einer Bestellposition der Artikel, legt der Import ihn aus dem Shop-Produkt an (Name, EAN als Barcode, Artikelsteuervorlage über die Steuerzuordnung, Brutto-Shoppreis in der Standard-Verkaufspreisliste der Vertriebseinstellungen). Hat das Produkt im Shop keinen Preis oder gibt es keine passende Verkaufspreisliste (Euro), wird der Artikel ohne Preis angelegt; der Grund steht im Protokoll. Der Preis dient nur zur Information, die Rechnung nimmt immer den Preis der Bestellung. **Voraussetzung:** Der Steuersatz des Produkts muss in der Steuerzuordnung stehen; sonst wird kein Artikel angelegt und die Bestellung als Fehler gemeldet. Jeder angelegte Artikel steht im Synchronisationsprotokoll (Vorgang „Artikel“). |
| Artikelgruppe für neue Artikel | Produkte | Artikelgruppe der angelegten Artikel. Leer: Standard-Artikelgruppe der Lagereinstellungen. |
| Neue Artikel sind Lagerartikel | an | Angelegte Artikel führen Lagerbestand. **Es wird kein Anfangsbestand gebucht**: bis Bestand gebucht ist, scheitert das Buchen einer Rechnung, die den Bestand ausbucht. |

Abschnitt *Steuern und Zahlungen*:

| Feld | Beispiel | Bedeutung |
| --- | --- | --- |
| Versandartikel | VERSAND | Artikel ohne Lagerführung für die Versandkostenzeile(n). Muss alle Artikelsteuervorlagen der Steuerzuordnung aufführen. |
| Umsatzsteuervorlage | Inland - MF | Vorlage mit einer Zeile je Steuersatz (Abschnitt 2). Die Steuern gelten als im Preis enthalten. |
| Steuerzuordnung | 19 → USt 19 %, 7 → USt 7 % | Tabelle: *Steuersatz (%)* wie in den Bestellpositionen des Shops und die *Artikelsteuervorlage* dafür. Ein Steuersatz ohne Zeile lässt die Bestellung scheitern. Nach „Shop-Daten laden“ ergänzt die Schaltfläche **Steuersätze des Shops ergänzen** in der Tabelle die fehlenden Steuersätze; Sie wählen dann nur noch die Artikelsteuervorlage. |
| Zahlungszuordnung | siehe unten | Tabelle: *Zahlungsklasse* (Code der Zahlungsart im Shop; nach „Shop-Daten laden“ werden die Codes vorgeschlagen), *Zahlungsweise* in ERPNext und *Bei Bestellung bezahlt*. Optional: Bestellungen mit einer Zahlungsklasse ohne Zeile werden trotzdem importiert, nur ohne Zahlung. |

Beispiel für die **Zahlungszuordnung**:

| Zahlungsklasse | Zahlungsweise | Bei Bestellung bezahlt |
| --- | --- | --- |
| `paypal` | PayPal | an – die Zahlung wird beim Buchen der Rechnung erstellt |
| `moneyorder` | Überweisung | aus – Vorkasse; die Rechnung bleibt offen, bis der Bankeingang zugeordnet ist |
| `invoice` | Überweisung | aus – Kauf auf Rechnung |

Die Groß-/Kleinschreibung der Zahlungsklasse spielt keine Rolle. Die *Zahlungsweise* ist in jeder Zeile
Pflicht, wird aber nur bei *Bei Bestellung bezahlt* verwendet.

### Reiter „Synchronisierung“

| Feld | Beispiel | Bedeutung |
| --- | --- | --- |
| Status-IDs für den Import | `1,2` | Nach „Shop-Daten laden“ wählen Sie die Status mit **Import-Status auswählen** per Häkchen nach Namen (gespeichert werden die IDs, durch Komma getrennt). Bestellungen im Status *Status-ID Versendet* werden immer importiert, auch ohne Häkchen. Bestellungen in einem anderen Status warten (Eintrag „Warte auf Importstatus“) und werden importiert, sobald sie innerhalb des Wiederholungszeitraums einen dieser Status erreichen. |
| Status-ID Storniert | Storniert (4) | Nach „Shop-Daten laden“ per Namen wählbar. Stornierte Bestellungen werden nie importiert. Wird eine schon importierte Bestellung storniert, erscheint ein Fehler im Protokoll, damit Sie die Rechnung von Hand bearbeiten. Das wird nur erkannt, solange die Bestellung im *Wiederholungszeitraum* liegt (also noch abgerufen wird); spätere Stornierungen im Shop bemerkt ERPNext nicht. |
| Status-ID Versendet | Versendet (3) | Nach „Shop-Daten laden“ per Namen wählbar. Wird beim Senden einer Sendungsnummer gesetzt, wenn die Bestellung noch in einem Import-Status ist (nie rückwärts: ein anderer Status bleibt unverändert). Bestellungen mit diesem Status werden immer importiert. |
| DHL-Versanddienstleister-ID | DHL (1) | Nach „Shop-Daten laden“ per Namen wählbar. ID von DHL in der Versanddienstleister-Liste des Shops; wird mit der Sendungsnummer übertragen, damit der Shop den richtigen Sendungsverfolgungs-Link zeigt. |
| Rechnungen buchen | aus während der Testphase | An: importierte Rechnungen werden sofort gebucht (mit Zahlung und ggf. Lieferschein). Aus: sie bleiben Entwürfe, die Sie prüfen und selbst buchen. |
| Bestand synchronisieren | erst nach dem Go-Live an | An: der Bestand des Lagers wird an den Shop gemeldet und **überschreibt** die Menge im Shop. Erst einschalten, wenn der Anfangsbestand in ERPNext stimmt. |

## 5. Shop-Daten laden und Artikel übernehmen

### 5a. Shop-Daten laden

Speichern Sie die Verbindung, führen Sie **Verbindung testen** aus und klicken Sie dann im Reiter
„Synchronisierung“ ganz oben auf **Shop-Daten laden** (die Schaltfläche steht auch unter *Aktionen*). ERPNext
holt Bestellstatus, Versanddienstleister, Zahlungsklassen und Steuersätze aus dem Shop. Ein Fenster zeigt je
Liste, wie viele Werte geladen wurden oder warum eine Liste nicht geladen werden konnte. Eine Liste, die nicht
geladen wurde, behält ihre bisherigen Werte. Die geladenen Werte stehen im eingeklappten Abschnitt „Geladene
Shop-Daten“ (*Shop-Werte*, *Shop-Daten geladen am*). Laden Sie sie erneut, wenn Sie im Shop Status,
Versanddienstleister, Zahlungsmodule oder Steuersätze geändert haben.

Danach brauchen Sie keine IDs mehr nachzuschlagen:

- *Status-ID Storniert*, *Status-ID Versendet* und *DHL-Versanddienstleister-ID* wählen Sie aus einer Liste mit
  Namen aus, z. B. „Storniert (4)“. Gespeichert wird die ID. Steht im Feld eine ID, die nicht in den geladenen
  Daten vorkommt, weist ein Hinweis darauf hin.
- *Status-IDs für den Import*: **Import-Status auswählen** öffnet eine Liste mit einem Häkchen je Status. Der
  Status *Versendet* wird auch ohne Häkchen importiert.
- In der *Zahlungszuordnung* schlägt die Spalte *Zahlungsklasse* die Codes des Shops vor. Die Namen sind die
  Codes der Zahlungsmodule; der Shop liefert keine Klartextnamen. Es sind die installierten Module und die
  Zahlungsarten der letzten Bestellungen.
- In der *Steuerzuordnung* listet der Hinweis die Steuersätze des Shops, und **Steuersätze des Shops ergänzen**
  fügt die fehlenden Zeilen ein.

**Fallback: IDs von Hand eintragen.** Liefert ein Shop eine Liste nicht (ältere Version, fehlende Rechte des
API-Benutzers), meldet das Fenster das, und die Felder bleiben normale Eingabefelder, in die Sie die ID bzw. den
Code direkt eintragen. In einer Standardinstallation von modified sind es typischerweise:

| Bestellstatus | ID |
| --- | --- |
| Offen | 1 |
| In Bearbeitung | 2 |
| Versendet | 3 |
| Storniert | 4 |

Die IDs und Codes stehen in der Shop-Administration (Bestellstatus, Zahlungsmodule, Versanddienstleister; wo
genau, hängt von Version und Einrichtung Ihres Shops ab) oder für technisch Versierte in der REST-API (nach
`POST /api/v1/oauth`): `GET /api/v1/orders/status`, `GET /api/v1/shipping/carriers`, und im Feld
`payment_class` von `GET /api/v1/orders`. Ohne Technik hilft ein erster Import mit ausgeschaltetem
*Rechnungen buchen*: Der Code der Zahlungsart steht bei jeder Rechnung im Feld *Modified Payment Class*, und
wartende Bestellungen stehen im Synchronisationsprotokoll mit „Warte auf Importstatus (aktueller Status 3)“;
die Zahl ist die Status-ID.

### 5b. Fehlende Artikel aus Shop übernehmen

Artikel, die es im Shop, aber noch nicht in ERPNext gibt, legen Sie nicht von Hand an. Es gibt zwei Wege.

**Gezielt, vor dem Start:** Menü **Aktionen** → **Fehlende Artikel aus Shop übernehmen** (nur bei gespeichertem
Shop). Ein Fenster listet alle Shop-Produkte, zu denen es keinen Artikel gibt (Artikelnummer, Name, EAN, Preis).
Setzen Sie Häkchen (oder **Alle auswählen**) und klicken Sie **Übernehmen**. Danach sehen Sie, welche Artikel
angelegt wurden, welche schon vorhanden waren und bei welchen Produkten ein Fehler auftrat (mit Grund). Bei
mehr als 50 Produkten läuft die Übernahme im Hintergrund; das Ergebnis steht dann im Synchronisationsprotokoll.
Sie brauchen dafür das Recht, Artikel anzulegen. Ein zweiter Klick legt nichts doppelt an.

**Automatisch beim Import:** Ist im Reiter „Artikel, Steuern & Zahlungen“ **Fehlende Artikel beim Import
anlegen** eingeschaltet, legt der Import den Artikel zu einer Bestellposition selbst an, statt die Bestellung
als Fehler abzulehnen. Scheitert die Bestellung aus anderem Grund, wird auch der Artikel nicht angelegt und beim
nächsten Lauf erneut versucht.

In beiden Fällen gilt:

- Übernommen werden Artikelnummer (als Artikel-Code bzw. Barcode, je nach *Artikelabgleich*), Name,
  Beschreibung und die EAN als Barcode (nur eine gültige EAN, die noch kein anderer Artikel hat).
- Die *Artikelgruppe für neue Artikel* bestimmt die Gruppe (leer: Standard-Artikelgruppe der
  Lagereinstellungen). *Neue Artikel sind Lagerartikel* bestimmt, ob die Artikel Lagerbestand führen; schalten Sie
  es aus, wenn der Shop Streckengeschäfts- oder Nicht-Lagerprodukte verkauft.
- Der **Bruttopreis** aus dem Shop kommt in den Artikelpreis der Standard-Verkaufspreisliste aus den
  Vertriebseinstellungen (sonst „Standard Selling“). Die Preisliste muss in Euro sein. Hat das Produkt keinen
  Preis oder passt die Preisliste nicht, wird der Artikel ohne Preis angelegt und der Grund steht im Protokoll.
- **Steuerzuordnung:** Der Steuersatz des Produkts muss in der *Steuerzuordnung* stehen; daraus ergibt sich die
  Artikelsteuervorlage des Artikels. Fehlt der Steuersatz, wird für dieses Produkt **kein** Artikel angelegt
  (sonst würde die Umsatzsteuer falsch gebucht), und es erscheint ein Fehler.
- **Der Anfangsbestand wird nicht gebucht.** Buchen Sie ihn selbst (Abschnitt 2). Bis dahin scheitert das Buchen
  einer Rechnung, die den Bestand ausbucht, mit der Meldung zu fehlendem Bestand (Abschnitt 8).
- Jeder angelegte Artikel steht im Synchronisationsprotokoll mit dem Vorgang „Artikel“.

## 6. Erster Start

1. Stammdaten aus Abschnitt 2 anlegen, den Modified-Shop wie in Abschnitt 4 ausfüllen (noch **nicht**
   aktiviert) und speichern. Oben im Formular steht: „Füllen Sie die Reiter aus, testen Sie die Verbindung und
   aktivieren Sie dann den Shop.“
2. Menü **Aktionen** → **Verbindung testen**. Bei Erfolg erscheint „Verbindung erfolgreich“ mit den Daten des
   API-Benutzers (Kundennummer, E-Mail, Vor- und Nachname). Diese Schaltfläche dürfen nur Benutzer mit der
   Rolle System-Manager verwenden.
3. **Shop-Daten laden** (Abschnitt 5a) und danach im Reiter „Synchronisierung“ die Status, den
   DHL-Versanddienstleister, die Zahlungs- und die Steuerzuordnung nach Namen auswählen. Danach die fehlenden
   Artikel übernehmen (Abschnitt 5b) und deren Anfangsbestand buchen.
4. *Import ab* prüfen (Ihr Starttermin), **Aktiviert** einschalten und speichern. Ab jetzt läuft der Import
   automatisch alle 5 Minuten.
5. Wer nicht warten will: **Aktionen** → **Bestellungen jetzt importieren**. Es erscheint „Import gestartet –
   die Ergebnisse erscheinen im Synchronisationsprotokoll und als Ausgangsrechnungen“ (oder „Import läuft
   bereits“, wenn gerade ein Import wartet oder läuft). Der Import läuft im Hintergrund; laden Sie das
   Formular nach einer Weile neu.
6. Ergebnis prüfen: In der **Statuszeile** oben im Formular steht dann z. B. „Letzter Import: vor 2 Minuten ·
   3 in den letzten 7 Tagen importiert · 1 warten auf den Importstatus · 0 Importfehler“, darunter Links zum
   Synchronisationsprotokoll, zu den Ausgangsrechnungen dieses Shops, zum Integrations-Protokoll (falls
   installiert) und zu dieser Anleitung. Die Zeile ist grün, bei Importfehlern rot.
7. Vor dem ersten Buchen: Für alle Lagerartikel muss der **Anfangsbestand** im Shop-Lager gebucht sein
   (Abschnitt 2, mit Buchungsdatum vor den Bestelldaten). Sonst lehnt ERPNext das Buchen mit „… Einheiten
   von … benötigt in … zum Abschluss dieser Transaktion.“ ab.
8. Die **Ausgangsrechnungen** (Entwürfe) prüfen: Kunde, Adressen, Positionen, Steuern, Gesamtbetrag. Buchen
   Sie einige von Hand und prüfen Sie Zahlung und Lagerabgang bzw. Lieferschein.
9. Wenn alles stimmt: **Rechnungen buchen** einschalten.
10. Wenn der Anfangsbestand in ERPNext stimmt: **Bestand synchronisieren** einschalten und speichern. Danach
   erscheint unter **Aktionen** die Schaltfläche **Bestand jetzt abgleichen**; sie meldet den Bestand aller
   Lagerartikel, die es auch im Shop gibt, sofort an den Shop („Bestandsabgleich gestartet“).

Die Schaltflächen erscheinen erst, wenn der Shop gespeichert ist. „Bestellungen jetzt importieren“ und
„Bestand jetzt abgleichen“ verlangen einen aktivierten Shop („Aktivieren Sie zuerst den Shop.“).

## 7. Wo Sie die Ergebnisse finden

- **Ausgangsrechnungen**: eine je importierter Bestellung. Filtern Sie nach *Modified Order ID*, um eine
  bestimmte Shop-Bestellung zu finden.
- **Zahlungen** (Zahlungseingang): beim Buchen von Rechnungen mit *Bei Bestellung bezahlt*; die Bestellnummer
  steht als Referenz darin.
- **Lieferscheine**: nur bei Bestellungen mit Streckengeschäfts-Artikeln (siehe Abschnitt 1).
- **Kunden**: neu angelegte Shop-Kunden tragen den Shop-Namen im Feld *Modified-Shop*.
- **Modified-Synchronisationsprotokoll**: je Eintrag Shop, *Vorgang* (Importieren = Bestellimport, Lager =
  Bestandsmeldung, Sendungsverfolgung, Artikel = angelegter Artikel), *Referenz* (Bestellnummer bzw. Artikel-Code), *Status* (Erfolgreich,
  Fehler, Übersprungen) und der Meldungstext.
  Ein erfolgreicher Import verweist auf seine Rechnung. Erfolgreiche Bestandsmeldungen werden nicht
  protokolliert, nur Fehler und übersprungene Artikel. Derselbe Fehler wird nicht bei jedem Lauf erneut
  eingetragen.
- **Integrations-Protokoll**: Ist zusätzlich die App *Integration Log* installiert, erscheinen dort die Fehler
  und die wartenden Bestellungen (als Warnung) aller Anbindungen an einer Stelle. Wird eine Bestellung später
  importiert, wird der Eintrag dort automatisch erledigt.

## 8. Probleme finden und beheben

**Wiederholen.** Öffnen Sie einen Protokolleintrag mit Status *Fehler* (Vorgang Importieren) oder eine
wartende Bestellung („Warte auf Importstatus …“) und klicken Sie nach dem Beheben der Ursache auf
**Wiederholen**. Die Bestellung
wird sofort neu aus dem Shop geholt und importiert, auch wenn sie älter
als der Wiederholungszeitraum ist. Klappt es, wird der alte Eintrag auf *Übersprungen* gesetzt und verweist
auf den neuen. Ist die Bestellung inzwischen schon importiert, im Shop storniert, nicht mehr in einem
Import-Status oder vor *Import ab* aufgegeben, wird der Eintrag mit diesem Grund geschlossen. Eine Bestellung,
die immer noch auf ihren Status wartet, bleibt offen. Dieselbe Schaltfläche gibt es im Integrations-Protokoll.
Dafür brauchen Sie das Recht, Ausgangsrechnungen anzulegen.

Typische Meldungen im Synchronisationsprotokoll (die Texte erscheinen in der Systemsprache; einige technische
Meldungen sind immer englisch):

| Meldung (Beispiel) | Ursache | Was tun |
| --- | --- | --- |
| Kein Artikel gefunden für products_model HOLZ-ZUG-01 (Shop-Produkt 12, Holzeisenbahn) | Kein ERPNext-Artikel mit diesem Artikel-Code bzw. diesem Barcode. | Artikel anlegen oder Artikel-Code korrigieren, dann **Wiederholen**. |
| Artikel … für products_model … ist deaktiviert | Der passende Artikel ist deaktiviert. | Artikel aktivieren, dann **Wiederholen**. |
| Für den Steuersatz 7.00 % ist im Shop … keine Artikelsteuervorlage zugeordnet | In der Steuerzuordnung fehlt der Steuersatz. | Zeile in der *Steuerzuordnung* ergänzen, dann **Wiederholen**. |
| Shop-Produkt 12 (HOLZ-ZUG-01): keine Artikelsteuervorlage für Steuersatz 7 % in der Steuerzuordnung von Shop … (Vorgang Artikel) | Beim Anlegen eines Artikels aus dem Shop (Übernahme oder Import) fehlt der Steuersatz des Produkts in der Steuerzuordnung. Es wurde kein Artikel angelegt. | Zeile in der *Steuerzuordnung* ergänzen, dann erneut übernehmen bzw. **Wiederholen**. |
| Steuerklasse … hat im Shop mehrere Steuersätze (… %); ordnen Sie genau einen in der Steuerzuordnung zu | Das Produkt hat im Shop mehrere Steuersätze (z. B. je Steuerzone), und mehrere davon stehen in der Steuerzuordnung. | Nur den gewünschten Steuersatz in der Steuerzuordnung lassen oder den Artikel von Hand anlegen. |
| Zeile 2 (…): Erwartet wurde die Artikelsteuervorlage USt 7 % …, ERPNext hat … gesetzt | Der Artikel (oder der Versandartikel) führt die Vorlage nicht auf. | Vorlage im Reiter „Steuer“ des Artikels oder seiner Artikelgruppe ergänzen, dann **Wiederholen**. |
| Bestellung …: Rechnungs-Gesamtbetrag … stimmt nicht mit dem Shop-Gesamtbetrag … überein | Rechnung und Shop weichen um mehr als einen Cent ab. | Bestellung im Shop prüfen (Preise, Versand, Rabatte) und ggf. von Hand buchen. |
| Warte auf Importstatus (aktueller Status 3) | Kein Fehler: Die Bestellung hat (noch) keinen Status aus *Status-IDs für den Import*. | Abwarten; oder die Status-IDs anpassen bzw. den Status im Shop ändern und **Wiederholen**. |
| gift voucher redemption (ot_gv) is not supported | Ein Geschenkgutschein wurde eingelöst. | Bestellung von Hand buchen. |
| unsupported currency …, unsupported allow_tax (net prices), unsupported attributes on product …, unsupported total class … | Bestellung außerhalb der Grenzen dieser Version (Abschnitt 1). | Bestellung von Hand buchen. |
| guest order without email | Gastbestellung ohne E-Mail-Adresse. | Bestellung von Hand buchen. |
| Unbekannter Ländercode … | Das Land der Adresse ist in ERPNext nicht zu finden. | Ländercode der Adresse im Shop prüfen. |
| Bitte tragen Sie ein Bank- oder Kassenkonto in Zahlungsweise … ein | Der Zahlungsweise fehlt das Standardkonto für Ihr Unternehmen (im Protokoll bei eingeschaltetem *Rechnungen buchen*; beim Buchen von Hand erscheint die Meldung direkt). | Konto in der Zahlungsweise eintragen, dann **Wiederholen** bzw. erneut buchen. |
| 2 Einheiten von HOLZ-ZUG-01 benötigt in Lagerräume - MF zum Abschluss dieser Transaktion. (je nach Lage auch „Es werden 2 Einheiten von … in … auf … benötigt, um diesen Vorgang abzuschließen.“) | Beim Buchen (bei eingeschaltetem *Rechnungen buchen* oder von Hand) reicht der Bestand im Shop-Lager zum Bestelldatum nicht. | Bestand per Lagerbuchung oder Bestandsabgleich mit Buchungsdatum vor dem Bestelldatum einbuchen, dann **Wiederholen** bzw. erneut buchen. |
| Bestellung … wurde nach dem Import im Shop storniert; bitte Rechnung … prüfen | Die Bestellung wurde nach dem Import storniert. | Einen Rechnungsentwurf löschen; eine gebuchte Rechnung stornieren oder eine Gutschrift erstellen. |
| Bestellimport abgebrochen: Shop API error … (Referenz „(run)“) | Der Shop war nicht erreichbar oder hat die Anmeldung abgelehnt. | Basis-URL, Zugangsdaten und **Verbindung testen** prüfen. Der nächste Lauf holt alles nach; dieser Eintrag kann nicht wiederholt werden. |
| Der Artikel ist nicht im Shop | (Vorgang Lager) Zu diesem ERPNext-Artikel gibt es kein Shop-Produkt mit passendem Wert (Modell bzw. EAN). | Nur ein Hinweis; Artikel im Shop anlegen oder ignorieren. |
| Der Artikel hat keinen Wert, um ihn dem Shop-Produkt zuzuordnen | (Vorgang Lager) Beim Abgleich über Barcode hat der Artikel keinen Barcode. | Barcode am Artikel eintragen. |
| Vollständiger Bestandsabgleich abgebrochen: … | (Vorgang Lager) Die Produktliste des Shops konnte nicht abgerufen werden. | Verbindung prüfen; der nächste tägliche Abgleich oder **Bestand jetzt abgleichen** versucht es erneut. |

Andere, unerwartete Fehler erscheinen als „Fehlertyp: Text“ (z. B. „TypeError: …“); die technischen Details
stehen dann zusätzlich im **Fehlerprotokoll** (Error Log) unter „Import der Modified-Bestellung fehlgeschlagen“.

**Eine Zahlungsklasse fehlt in der Zahlungszuordnung?** Das ist kein Fehler: Die Bestellung wird importiert,
nur ohne Zahlung. Ergänzen Sie die Zeile, wenn für diese Zahlungsart künftig eine Zahlung entstehen soll.

**Rechnung storniert und berichtigt?** Eine berichtigte Shop-Rechnung behält die Bestellnummer. Beim Buchen
entstehen keine zweite Zahlung und kein zweiter Lieferschein; stattdessen weist ein Eintrag „Übersprungen“
darauf hin, die vorhandene Zahlung über den **Zahlungsabgleich** zuzuordnen bzw. die Lieferungen der
ursprünglichen Rechnung zu prüfen.

**Verbindungstest schlägt fehl?** Dann erscheint eine Fehlermeldung; ihr technischer Text enthält
„Shop API error“ und den HTTP-Status der Shop-Antwort. Prüfen
Sie die Basis-URL (ohne `/api/v1`, mit `https://`), Benutzername und Passwort des API-Benutzers und ob die
REST-API im Shop aktiv ist. „Shop API error None …“ bedeutet, dass der Shop gar nicht erreicht wurde
(Adresse, Netzwerk, Zertifikat).
