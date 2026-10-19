# Kurzanleitung: Mennekes AMTRON in Home Assistant

Diese Anleitung führt durch Einrichtung und Bedienung. Details zu Registern, Berechnung und
Fehlersuche stehen in der [README](README.md) (Englisch).

## 1. Wallbox vorbereiten

Im Webinterface der Wallbox als **Operator** anmelden:

1. **Lastmanagement**: **Modbus TCP** einschalten, als Registersatz **„Mennekes“** wählen und
   **„Modbus Slave Allow Start/Stop Transaction“** aktivieren.
2. **Autorisierung**: **„kostenloses Laden“** einschalten. Ohne diese Einstellung lehnt die
   Wallbox den Startbefehl der Integration ab wie eine unbekannte RFID-Karte.
3. Speichern und die Wallbox neu starten.

## 2. Installieren und hinzufügen

1. **HACS** → Menü **⋮** → **Benutzerdefinierte Repositories** → die URL dieses Repositorys mit
   der Kategorie **Integration** hinzufügen → **„Mennekes AMTRON Charge Control“** installieren →
   Home Assistant neu starten.
   Ohne HACS: den Ordner `custom_components/mennekes_amtron/` nach
   `<config>/custom_components/` kopieren und neu starten.
2. **Einstellungen → Geräte & Dienste → Integration hinzufügen** → „Mennekes AMTRON“ suchen →
   die IP-Adresse der Wallbox eingeben. Port `502` und Unit-ID `255` sind vorausgefüllt. Klappt
   die Verbindung mit 255 nicht, die Unit-ID `1` versuchen.

Beim Hinzufügen liest die Integration sofort von der Wallbox. Eine falsche Einstellung zeigt sich
deshalb direkt als Fehlermeldung im Dialog.

## 3. Einstellungen

**Einstellungen → Geräte & Dienste → Mennekes AMTRON → Konfigurieren**. Der Dialog hat zwei
Seiten. Nach dem Speichern lädt die Integration neu.

### Seite 1: Ströme und Phasenumschaltung

![Konfigurieren, Seite 1](screenshots/configure_options_de.png)

| Feld | Bedeutung |
|---|---|
| Maximaler Ladestrom | Obergrenze für alles, was die Integration schreibt. Passend zur Absicherung der Zuleitung einstellen (Standard 16 A). |
| Von „Ladevorgang pausieren“ geschriebener Wert | Normalerweise 0 A (Standard). |
| Von „Ladevorgang starten“ geschriebener Wert | Startladestrom, 6 A bis zum maximalen Ladestrom (Standard 16 A). |
| Phasenumschaltung | Nur mit Umschalt-Hardware in der Wallbox. Für Lademodi und PV-Überschussladen auf **„Deaktiviert“** lassen. |

### Seite 2: PV-Überschuss-Quellen (optional)

![Konfigurieren, Seite 2](screenshots/configure_pv_sources_de.png)

Hier wählst du Entities deines Wechselrichters (z. B. SMA) aus. Ohne Netzeinspeisung bleibt das
PV-Überschussladen aus, alles andere funktioniert trotzdem.

| Feld | Hinweis |
|---|---|
| Netzeinspeisung | Eine oder mehrere, die Werte werden addiert (z. B. Wechselrichter plus Balkonkraftwerk). Eine Anlage, die dein Netzzähler schon mitmisst, nicht zusätzlich eintragen, sonst zählt sie doppelt. |
| Netzbezug | Leer gelassen zählt als 0 W. |
| Speicher-Entladeleistung | Leer gelassen zählt als 0 W (kein Hausspeicher). |
| Speicher-Ladestand (SoC) | Ohne Hausspeicher leer lassen, dann gelten die SoC-Schwellen nicht. |

Alle Leistungen werden als positive Werte in W oder kW erwartet.

## 4. Das Gerät in Home Assistant

![Geräteseite](screenshots/device_page_de.png)

| Bereich | Entity | Wofür |
|---|---|---|
| Steuerelemente | Lademodus | PV-Überschuss, Sofort, Aus oder Manuell (siehe unten). |
| | Ladestrom-Grenze | Ladestrom von Hand setzen. 0 A heißt pausiert. |
| | Ladevorgang starten / pausieren | Startet mit dem Startladestrom bzw. pausiert mit dem Pausenwert. |
| Sensoren | Ladeleistung, Status, Energie (aktuelle Ladung), Gesamtenergie | Messwerte der Wallbox. Die Gesamtenergie eignet sich für das Energie-Dashboard. |
| | PV-Überschuss | Überschuss im 2-Minuten-Mittel. Nur mit PV-Quellen. |
| Konfiguration | PV: … und Sofort: … | Einstellungen der PV-Regelung (siehe unten). Nur mit PV-Quellen. |
| Diagnose | Laderegelung | Zeigt, was die Regelung gerade tut und warum. |
| | Fehler, Firmware-Version | Für die Fehlersuche. |

Mit aktiver Phasenumschaltung kommt die **Ladeleistung-Grenze** (W) dazu.

## 5. Laden im Alltag: der Lademodus

![Lademodus](screenshots/charge_mode_options_de.png)

| Modus | Was passiert |
|---|---|
| **PV-Überschuss** | Die Integration startet, regelt und stoppt die Ladung nach dem Überschuss (siehe nächster Abschnitt). |
| **Sofort** | Lädt mit dem maximalen Ladestrom. Ist das Auto voll, wechselt der Modus auf PV-Überschuss (abschaltbar). |
| **Aus** | Hält den Ladestrom auf 0 A. |
| **Manuell** *(Standard)* | Die Integration greift nicht selbst ein. Du steuerst über die Buttons und die Ladestrom-Grenze. |

- **Manuell eingreifen:** „Ladevorgang starten“, „Ladevorgang pausieren“ oder eine Änderung der
  Ladestrom-Grenze schalten den Modus auf **Manuell**. Sonst würde die Regelung deine Änderung
  innerhalb von 30 s wieder überschreiben.
- **Ausstecken:** Steht der Status mindestens 10 s auf „Frei“, geht „Manuell“ zurück auf
  **PV-Überschuss** (nur mit PV-Quellen). „Sofort“ und „Aus“ bleiben, bis du sie änderst.
- **Auto voll bei „Sofort“:** Die Wallbox kennt den Akkustand des Autos nicht. Als voll gilt das
  Auto, wenn es 10 Minuten lang selbst pausiert („Pausiert (Fahrzeug)“), obwohl Strom angeboten
  wird. Pausiert dein Auto auch aus anderen Gründen, z. B. wegen eines eigenen Ladetimers, den
  Schalter **„Sofort: danach zurück zu PV-Überschuss“** ausschalten.

## 6. So entscheidet die PV-Regelung

Die Regelung läuft alle 30 s und rechnet mit einphasigem Laden, also 230 W pro Ampere.

- **Start**, wenn der Speicher mindestens den Start-SoC hat **und** im 2-Minuten-Mittel
  mindestens 1380 W (6 A) Überschuss da sind.
- **Ladestrom** = Überschuss ÷ 230 W, abgerundet, zwischen 6 A und dem maximalen Ladestrom.
- **Unter 6 A Überschuss** lädt sie mit 6 A weiter und überbrückt den Rest aus dem Speicher, aber
  nur für die Pufferzeit. Danach stoppt sie. Der Speicher puffert also nur Wolken und wird nicht
  ins Auto entladen.
- **Stopp**, sobald der Speicher unter den Stopp-SoC fällt. Neu gestartet wird erst wieder ab dem
  Start-SoC.
- Lädt die Wallbox beim Einstecken von selbst los, übernimmt die Regelung die Ladung, wenn die
  Startbedingung erfüllt ist, und stoppt sie sonst.
- Fehlen Werte (Quelle oder Wallbox nicht verfügbar, oder weniger als 1 Minute seit dem Start),
  ändert sie nichts und der letzte Ladestrom bleibt.

Die Einstellungen dazu findest du auf der Geräteseite unter **Konfiguration**:

| Einstellung | Standard | Bedeutung |
|---|---|---|
| PV: Start ab Speicher-SoC | 60 % | Unter diesem Ladestand startet keine PV-Ladung. |
| PV: Stopp unter Speicher-SoC | 50 % | Darunter stoppt die Ladung. Muss unter dem Start-SoC liegen. |
| PV: Mindestabstand Start/Stopp | 90 s | Mindestzeit zwischen zwei Start- oder Stoppbefehlen, schont Auto und Wallbox. |
| PV: Pufferzeit unter 6 A | 10 min | So lange darf der Speicher bei weniger als 6 A Überschuss aushelfen. |
| Sofort: danach zurück zu PV-Überschuss | an | Wechselt von „Sofort“ auf PV-Überschuss, wenn das Auto voll ist. |

## 7. Was zeigt die „Laderegelung“?

| Anzeige | Bedeutung |
|---|---|
| Manuell / Aus / Sofortladen | Der gewählte Modus ist aktiv. |
| Kein Fahrzeug | Kein Auto eingesteckt. |
| Wartet auf Speicher-SoC | Der Speicher hat den Start-SoC noch nicht erreicht. |
| Wartet auf Überschuss | Weniger als 1380 W Überschuss im 2-Minuten-Mittel. |
| Lädt mit PV-Überschuss | Die Regelung lädt und führt den Strom nach. |
| Puffert aus Speicher | Weniger als 6 A Überschuss, die Pufferzeit läuft. |
| Mindestabstand läuft | Ein Start oder Stopp ist fällig, wartet aber noch auf den Mindestabstand. |
| Wartet auf Daten | Eine Quelle, der SoC oder die Wallbox ist nicht verfügbar, oder die Integration läuft noch keine Minute. |
| Keine PV-Quellen konfiguriert | PV-Überschuss ist gewählt, aber auf Seite 2 von „Konfigurieren“ fehlt die Netzeinspeisung. |
| Phasenumschaltung aktiv | Die Phasenumschaltung steht nicht auf „Deaktiviert“, die Regelung bleibt aus. |
| Fehler | Ein Schreibbefehl an die Wallbox ist fehlgeschlagen. Details stehen im Home-Assistant-Log. |

## 8. Wenn etwas nicht klappt

- **„Ladevorgang starten“ wirkt nicht:** In der Wallbox „Modbus Slave Allow Start/Stop
  Transaction“ und „kostenloses Laden“ prüfen (Schritt 1).
- **Der Lademodus springt auf „Manuell“:** Das ist gewollt, sobald du selbst startest, pausierst
  oder die Ladestrom-Grenze änderst.
- **PV-Überschuss lädt nicht los:** Die Laderegelung zeigt den Grund. Die Attribute des Sensors
  „PV-Überschuss“ zeigen die einzelnen Werte der letzten Messung, die du mit der App deines
  Wechselrichters vergleichen kannst.
- Weitere Fehlerbilder stehen in der [README unter Troubleshooting](README.md#troubleshooting).
