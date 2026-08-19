# H@H Observer

`hath_observer` is a small standard-library-only sidecar for the official
Hentai@Home downloader. It watches the downloader's output directory and
optional `log_out`, keeps a durable SQLite outbox, and sends idempotent events
to `exhentai-self-rec`.

It supports Python 3.6 and newer so it can run on the Ubuntu 18.04 generation
of H@H hosts without installing an additional runtime.

It does not read the H@H client key, call the private H@H RPC protocol, modify
the cache, or require an inbound port on the downloader machine.

## What it observes

- Download directories named by the official client as `Title [gid]` or
  `Title [gid-1280x]`.
- The numeric fallback directory names used when the title cannot be created.
- File count and byte progress.
- Completion through the presence of `galleryinfo.txt`.
- Start, per-page progress, permanent failure, low disk suspension, and idle
  messages when a live `log_out` is available.
- Optional process liveness through a PID or PID file.
- Aggregated `/h/` image-serving request counts and bytes for five-minute,
  one-hour, and durable total windows.
- Proxy tests tracked separately from image traffic, plus interrupted
  handshakes, cache utilization, JVM memory, and systemd process uptime.

Only numeric aggregates and timestamps are sent for serving activity. Remote
IP addresses, cache request paths, RPC URLs, client keys, and action keys are
never stored in the observer database or included in heartbeat events.

The full server-side H@H queue is not visible to an unmodified client. The
observer sees a task once the client starts it or creates its output directory.

## Receiver setup

Set a long random token before starting the recommendation server:

```powershell
$env:EXH_REC_HATH_TOKEN = "replace-with-a-long-random-value"
python -m exh_rec.app
```

The receiver exposes:

- `POST /api/integrations/hath/events` for authenticated observer batches.
- `GET /api/integrations/hath/status` for the latest clients and downloads.

The server uses completed downloads as implicit positive labels with a default
weight of `1.25`. Explicit feedback and Favorite/Ban marks take precedence.
Failed transfers are operational events, never negative preference labels.

## First local run

Use a temporary directory to verify configuration without reporting historical
downloads:

```powershell
python -m hath_observer `
  --download-dir "D:\HentaiAtHome\download" `
  --log-file "D:\HentaiAtHome\log\log_out" `
  --server-url "http://127.0.0.1:18787" `
  --token "replace-with-a-long-random-value" `
  --client-id "hath-main" `
  --once
```

The first scan records existing directories as a baseline. Add `--backfill`
only when existing completed downloads should be imported as recommendation
signals.

For continuous observation, remove `--once`. The default scan interval is ten
seconds and heartbeat interval is sixty seconds:

```powershell
python -m hath_observer `
  --download-dir "D:\HentaiAtHome\download" `
  --log-file "D:\HentaiAtHome\log\log_out" `
  --server-url "http://recommendation-host:18787" `
  --token "replace-with-a-long-random-value" `
  --client-id "hath-main"
```

Equivalent environment variables are available:

| Variable | Meaning |
| --- | --- |
| `HATH_DOWNLOAD_DIR` | Official H@H download directory |
| `HATH_LOG_FILE` | Optional `log_out` path |
| `HATH_CLIENT_ID` | Stable name for this client |
| `HATH_OBSERVER_STATE` | Observer SQLite state path |
| `HATH_OBSERVER_INTERVAL` | Scan interval in seconds |
| `EXH_REC_URL` | Recommendation server base URL |
| `EXH_REC_HATH_TOKEN` | Shared bearer token |
| `HATH_PID_FILE` | Optional file containing the Java PID |
| `HATH_SYSTEMD_UNIT` | Optional systemd unit whose active state reports Java process liveness |

The default state database is `~/.hath-observer/state.sqlite3`. Do not delete
or replace it during ordinary upgrades: it contains the delivery outbox and the
baseline that prevents old downloads from being reported again.

## systemd deployment

The repository includes a hardened unit and environment template under
`hath_observer/deploy`. The deployed layout is:

- `/srv/hath-observer` for the Python package.
- `/etc/hath-observer.env` for paths, receiver URL, and the shared token.
- `/var/lib/hath-observer/state.sqlite3` for the durable baseline and outbox.
- `hath-observer.service` for automatic startup and restart.

The environment file must be mode `0600`. Keep the state database when
upgrading; replacing it would make the next run establish a new baseline.

## Logging caveat

The official Java client buffers `log_out` unless log flushing is enabled, so
directory progress remains the authoritative source. The observer tails new
log content when it becomes visible and handles truncation or rotation. It
starts at the end of an existing log on first launch to avoid replaying old
messages.

## Network placement

The observer only initiates outbound HTTP requests. On an untrusted network,
put the connection behind HTTPS, Tailscale, or WireGuard in addition to the
bearer token. The event payload contains directory names but never H@H client
keys or ExHentai cookies.

When the recommendation server is a local PC without an inbound address, bind
a reverse SSH listener to loopback on the H@H host and point the observer at
that listener. The bundled Windows launcher supervises this topology:

```text
observer -> H@H 127.0.0.1:18788 -> reverse SSH -> local rec 127.0.0.1:18787
```

Because the observer outbox is durable, events remain queued while either rec
or the SSH tunnel is unavailable and are delivered after the connection
returns.
