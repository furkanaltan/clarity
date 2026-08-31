# Rov.E Dependencies

Stand: 31.08.2026. Die bestehende `requirements-auth.txt` deckt nur Argon2 ab
und ist keine vollstaendige reproduzierbare Umgebung.

## Python Runtime

- Produktion API: Python 3.12 in `/root/rove-app-api-venv`
- Produktion Bot und einzelne Worker: `/usr/bin/python3`
- Aktuelle lokale Testumgebung: Python 3.14

Diese Laufzeiten werden in Wave 3 nicht vereinheitlicht.

## Direct Dependencies

| Paket/Modul | Verwendet von |
|---|---|
| `argon2-cffi` | API und Auth-Tests |
| `APScheduler` | `bot.py` |
| `python-dotenv` | Bot und Report-Renderer |
| `Flask` | API und historisches Dashboard |
| `Jinja2` | HTML-, Web- und Light-Renderer |
| `openai` | Bot und AI-Reporttext |
| `pywebpush` | API-Push |
| `pyTelegramBotAPI` (`telebot`) | Telegram-Bot |
| `reportlab` | Report-Engine und PDF-Renderer |
| `WeasyPrint` | HTML-/Web-/Light-PDF-Rendering |

Die exakten produktiven Versionen sind noch nicht vollstaendig aus beiden
Server-Laufzeiten erfasst. Deshalb werden in Wave 3 keine erfundenen Pins und
kein lokales `pip freeze` als Produktionswahrheit committed.

## Optional Dependencies

- `openai` ist nur fuer AI-Funktionen erforderlich; deterministische
  Kernfunktionen muessen ohne Provideraufruf weiterlaufen koennen.
- `pywebpush` ist nur fuer Web-Push erforderlich.
- Report-Pakete wie `reportlab`, `Jinja2` und `WeasyPrint` sind fuer die API nur
  dort erforderlich, wo Reportmodule importiert oder gerendert werden.

Ob diese Pakete in Produktion getrennt installiert werden koennen, ist ohne
vollstaendige Runtime-Pruefung `UNKNOWN`.

## Dev And Test Dependencies

- `argon2-cffi` wird direkt von Auth-Tests genutzt.
- Python-Standardbibliothek `unittest` ist das bestehende Testframework; es
  wird kein neues Framework eingefuehrt.
- Node.js wird fuer `node --check` gegen das extrahierte Frontend-JavaScript
  benoetigt.
- Lokale Tests brauchen je nach Teilmenge dieselben Pakete wie API, Bot oder
  Reports. Exakte Dev-Versionen sind `UNKNOWN`.

## System Dependencies

- SQLite
- systemd und Nginx in Produktion
- Node.js fuer den JavaScript-Syntaxcheck
- WeasyPrint-Laufzeitbibliotheken und Fonts fuer reproduzierbares PDF-Rendering
- `curl` und Systemwerkzeuge fuer Health- und Deploymentpruefungen

Die plattformspezifischen WeasyPrint-Pakete und Fonts muessen auf dem Server
separat inventarisiert werden, bevor ein reproduzierbares Setup als vollstaendig
gilt.

## Empfohlene spaetere Struktur

```text
requirements/base.txt
requirements/api.txt
requirements/bot.txt
requirements/reports.txt
requirements/dev.txt
```

Versionen duerfen erst nach Abgleich der aktiven API-, Bot- und Worker-Umgebung
festgeschrieben werden. Das Erstellen dieser Dateien ist ein eigenes,
getestetes Changeset.

## Aktueller Reproduzierbarkeitsstatus

- Source- und Testeinstieg: dokumentiert
- Python-Abhaengigkeiten: statisch inventarisiert
- Exakte produktive Versionen: unvollstaendig
- Native Report-Abhaengigkeiten: unvollstaendig
- Vollstaendiger Clean-Room-Aufbau: noch nicht garantiert
