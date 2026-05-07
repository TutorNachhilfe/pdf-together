# pdf-together

Eine kollaborative PDF-Annotations-App für den Unterricht im lokalen Netzwerk (LAN). Lehrer und Schüler können gleichzeitig im Browser auf derselben PDF zeichnen.

## Installation

```bash
pip install websockets
python server.py
```

Beispielausgabe:

```text
✅ Server läuft auf http://192.168.1.42:8080
🔑 Token: ABC12345
👨‍🏫 Lehrer-URL: http://192.168.1.42:8080/?token=ABC12345&role=teacher
📱 QR-Code:
...
```

## Features

- HTTP-Server auf `8080` + WebSocket-Server auf `8081`
- Bindung nur auf erkannte LAN-IP
- Session-Token als Zugriffsschutz
- Kollaboratives Zeichnen in Echtzeit
- Lehrer rot, Schülerfarben zufällig
- Undo (eigene Striche), Radiergummi, Seite löschen (nur Lehrer)
- Mehrere PDFs als getrennte Rooms/Tabs
- Teilnehmeranzeige (Schülerzahl)

## Sicherheitshinweise

- Nur für LAN-Betrieb gedacht.
- Kein HTTPS (`http://`/`ws://`) – keine sensitiven Daten übertragen.
- Token ist ein Sitzungszugang, kein vollwertiges Benutzer-/Rechtesystem.
- Nur in vertrauenswürdigem Schulnetz einsetzen.

## KI-Hinweis

Diese Anwendung wurde vollständig von GitHub Copilot (KI) erstellt und **nicht von einem Menschen geprüft**.
