# Ida-Reminder

MCP-Server, mit dem Claude sich selbst zeitversetzt an eine Aufgabe erinnern
kann -- "erinnere mich um 15 Uhr daran, X zu tun" fuehrt dazu, dass 5 Minuten
vor 15 Uhr automatisch eine claude.ai-Routine ausgeloest wird, die die
Aufgabe ausfuehrt. Laeuft wie die anderen Ida-*-Server als Docker-Container
(Dockge) hinter einem Cloudflare Tunnel, abgesichert per Bearer-Token.

## Wie es funktioniert

Es gibt beliebig viele unabhaengige **Plaetze** -- kein Limit wird irgendwo
eingestellt, der Server erkennt beim Start selbst, wie viele es gibt, einfach
an der Anzahl vollstaendig befuellter `REMINDER_SLOT_<N>_ROUTINE_ID`/`_API_KEY`-
Paare in der `.env`. Jeder Platz braucht einen eigenen
claude.ai-Routinen-API-Trigger (`routine_id` + Bearer-Token) -- deswegen
mehrere Plaetze und nicht nur einer: so koennen mehrere Erinnerungen
gleichzeitig "in der Luft" sein, ohne dass sich zwei Ausloesungen gegenseitig
blockieren (eine einzelne claude.ai-Routine kann nicht sinnvoll zweimal
parallel laufen).

1. `erinnerung_erstellen(zeitpunkt, aufgabe)` sucht sich automatisch den
   naechsten freien Platz, speichert die Aufgabe dort und berechnet den
   Ausloesezeitpunkt (`zeitpunkt` minus `VORLAUF_MINUTEN`). Das ist der
   einzige Tool-Aufruf, der fuer den Normalfall noetig ist.
2. Ein Hintergrund-Thread im Server prueft alle `POLL_INTERVALL_SEKUNDEN`,
   ob ein Platz faellig ist, und loest dann per HTTP die zu diesem Platz
   gehoerende claude.ai-Routine aus (dieselbe API wie bei Ida-Telegrams
   Auto-Reply).
3. Die ausgeloeste Routine bekommt nur eine kurze Nachricht, die sie
   anweist, `erinnerungen_liste(platz=N)` aufzurufen, um die eigentliche
   Aufgabe nachzulesen, und diese dann auszufuehren.
4. War es eine einmalige Aufgabe, ruft die Routine danach selbst
   `erinnerung_leeren(platz=N)` auf -- der Platz ist damit sofort wieder
   frei fuer eine ganz andere, spaetere Erinnerung.

Weil die eigentliche Aufgabe erst zur Laufzeit aus dem Platz gelesen wird,
koennen **alle Routinen denselben, komplett generischen System-Prompt**
haben -- z.B.:

> Du bist eine Ida-Reminder-Ausfuehrungs-Routine. Wenn du ausgeloest wirst,
> steht in der Nachricht, welches Ida-Reminder-Tool du aufrufen sollst, um
> deine Aufgabe zu erfahren. Folge dieser Anweisung, fuehre die Aufgabe aus,
> und raeume danach ggf. wie angewiesen auf.

Nur die API-Trigger (routine_id + Key) sind pro Platz unterschiedlich, nicht
der Inhalt der Routinen selbst.

## Tools

| Tool | Beschreibung |
|---|---|
| `erinnerung_erstellen` | Plant eine Erinnerung (`zeitpunkt`, `aufgabe`, optional `einmalig`, Standard `True`). Sucht sich selbst einen freien Platz. |
| `erinnerungen_liste` | Zeigt alle Plaetze mit Status (optional `platz=N` fuer nur einen -- so liest eine ausgeloeste Routine ihre eigene Aufgabe). |
| `erinnerung_leeren` | Gibt einen Platz wieder frei (Aufgabe abbrechen ODER nach Erledigung aufraeumen). **Kein** `bestaetigt`-Zwang -- siehe Sicherheit. |

## Sicherheit -- unbedingt lesen

- **`erinnerung_leeren` hat bewusst keine Bestaetigungs-Sperre**, obwohl es
  Daten unwiderruflich loescht (anders als bei Ida-Google/-Homeassistant).
  Grund: eine automatisch ausgeloeste Routine muss sich nach einer
  einmaligen Aufgabe selbststaendig aufraeumen koennen, ohne dass jemand im
  Chat sitzt, der eine Rueckfrage beantworten koennte -- exakt dieselbe
  Abwaegung wie bei `ssh_befehl_ausfuehren` in Ida-SSH.
- **Die Routine-API-Keys in der `.env` (`REMINDER_SLOT_n_API_KEY`) sind
  scharf** -- wer sie hat, kann die jeweilige claude.ai-Routine beliebig oft
  mit beliebigem Text ausloesen. Nicht committen, nicht teilen.
- Der MCP-Endpunkt selbst ist wie bei allen Ida-*-Servern per
  `MCP_AUTH_TOKEN` abgesichert.

## Setup

### 1. Pro Platz eine claude.ai-Routine mit API-Trigger anlegen

Fuer jeden gewuenschten Platz (z.B. 3-5 reichen fuer den Alltag, beliebig
viele moeglich -- einfach weitere `REMINDER_SLOT_<N>_*`-Paare anlegen):

1. Auf [claude.ai/code/routines](https://claude.ai/code/routines) eine neue
   Routine anlegen, generischen System-Prompt wie oben rein.
2. In den Routine-Einstellungen einen **API-Trigger** hinzufuegen -- das
   zeigt eine `routine_id` (Prefix `trig_`) und generiert einen Bearer-Token
   (Prefix `sk-ant-oat01-`).
3. Beide Werte in die `.env`, z.B. fuer Platz 1:
   `REMINDER_SLOT_1_ROUTINE_ID=trig_...` und
   `REMINDER_SLOT_1_API_KEY=sk-ant-oat01-...`.
4. Diese Routine bei den claude.ai-Connectors mit dem Ida-Reminder-MCP-Server
   verbinden (Schritt 6), damit sie `erinnerungen_liste`/`erinnerung_leeren`
   aufrufen kann.

Wiederholen fuer jeden weiteren Platz. Ein neu generierter Token widerruft
den alten fuer denselben Trigger.

### 2. `.env` anlegen

```bash
cp .env.example .env
```

`MCP_AUTH_TOKEN` (z.B. `openssl rand -hex 32`), `TIMEZONE` falls nicht
Europe/Berlin, und die `REMINDER_SLOT_n_*`-Paare ausfuellen. `MCP_PORT`
ggf. anpassen, damit er sich von anderen Ida-*-Containern auf demselben
Server unterscheidet (Ida-Reminder default 8031).

### 3. Deployment (Dockge)

Wie die anderen Ida-*-Server: Stack anlegen, `docker-compose.yml` und `.env`
einspielen, `docker compose up -d`. Das `/data`-Volume merkt sich geplante
Erinnerungen ueber Neustarts hinweg.

### 4. Cloudflare Tunnel

Ingress-Regel wie bei den anderen Projekten: gewaehlter Hostname ->
`http://10.7.0.1:<MCP_PORT>`.

### 5. Als MCP-Server verbinden

Per CLI (Header-Variante):

```
claude mcp add --transport http ida-reminder https://<dein-hostname>.kulbarts.com/mcp --header "Authorization: Bearer <MCP_AUTH_TOKEN>"
```

Fuer den claude.ai-Connector (nimmt nur eine URL, keine eigenen Header) das
Token stattdessen als Query-Parameter mitgeben -- wird von derselben
BearerAuthMiddleware wie bei allen anderen Ida-*-Servern akzeptiert:

```
https://<dein-hostname>.kulbarts.com/mcp?token=<MCP_AUTH_TOKEN>
```

Fuer die claude.ai-Routinen aus Schritt 1: ebenfalls als Connector mit einer
dieser beiden URL-Varianten hinzufuegen, damit die ausgeloeste Routine
`erinnerungen_liste` und `erinnerung_leeren` aufrufen kann.

## Troubleshooting

- **"Alle N konfigurierten Plaetze sind aktuell belegt"**: `erinnerungen_liste`
  zeigt, welche Plaetze noch offen sind -- entweder warten, bis eine
  einmalige Aufgabe sich selbst aufraeumt, oder manuell mit
  `erinnerung_leeren` freigeben.
- **Erinnerung loest nicht aus**: Docker-Logs pruefen (`docker logs
  ida-reminder-mcp`) -- der Scheduler loggt jeden Ausloese-Versuch und
  Fehler; ein Netzwerkfehler beim Ausloesen wird beim naechsten
  Poll-Durchlauf automatisch erneut versucht, statt die Erinnerung
  stillschweigend zu verlieren.
- **Container startet nicht / "Konfigurationsfehler"**: `app/config.py`
  meldet in den Logs exakt, welche Variable fehlt oder ungueltig ist (z.B.
  ungueltige `TIMEZONE` oder ein Platz mit nur ID oder nur Key gesetzt).
