# BTC V5 forward observation — deployment and migration to a persistent host

Purpose: continue the EXISTING observation (start `2026-10-07 14:59:22 UTC`, freeze
`manifests/v5_forward_freeze.json`, frozen V5 config hash
`d18ebf19bd0cde67be1c27683d3e7ad0385d2456a6edaa1fe55146f013096177`) on a persistent Linux host
without creating a new freeze or observation period, without touching V5 logic, and without any
authenticated exchange access. Everything below is operational.

## 1. Requirements
| item | requirement |
|---|---|
| OS | Linux with systemd (Debian 12 / Ubuntu 22.04+ / RHEL 9 tested paths in `deploy/install.sh`) |
| Python | >= 3.12 (`pyproject.toml` `requires-python`); `uv python install 3.12` provides it if the OS lacks it |
| uv | 0.8.x (`curl -LsSf https://astral.sh/uv/install.sh \| sh`); `uv sync --all-extras --frozen` installs the pinned lockfile (`uv.lock`) |
| Python deps (from the lockfile) | pydantic 2, pydantic-settings, polars >= 1, numpy >= 1.26, typer, httpx, websockets >= 13, pyyaml, pyarrow (see `uv.lock` for exact pins) |
| system packages | git, curl, ca-certificates, logrotate (no database, no compiler) |
| network (outbound only) | `wss://stream.bybit.com` (public WS), `https://public.bybit.com` (archive seed, used only for days before the collector's first day), `https://github.com` for the checkout. Nothing inbound. No API keys. |
| CPU / RAM | 1 vCPU, 2 GB RAM are enough (one cycle: ~2-5 s, ~400 MB peak) |
| disk | ~0.5-0.6 GB/day of compressed raw data (6 topics, orderbook.50 dominates) + 2.9 GB seed; plan >= 40 GB free for 4 weeks |
| clock | NTP-synchronised UTC (latency statistics and bar closes depend on it) |

Environment variables (`/etc/btc-v5-forward.env`, template `deploy/btc-v5-forward.env.example`):
`BTC_DATA_DIR` (data root, default `/var/lib/btc_swing`), `PYTHONUNBUFFERED=1`, optional
`HTTPS_PROXY`, `HEALTH_STALE_SECONDS`, `HEALTH_BAR_STALE_SECONDS`, `HEALTH_MIN_FREE_GB`, `ALERT_CMD`.
No secret of any kind is required or read.

## 2. Directory structure on the host
```
/opt/btc_swing                      repository checkout (branch main, same commit as the source host)
  .venv/                            uv-managed virtualenv (service ExecStart uses .venv/bin/btc-swing)
  config/btc_swing_v5.yaml          FROZEN strategy (hash must equal the freeze record)
  config/btc_swing_v5_forward.yaml  collection/scheduling config
  manifests/v5_forward_freeze.json  the freeze record (migrated unchanged, never re-created)
  reports/forward/                  immutable daily snapshots (written by the runner at 00:05 UTC)
/var/lib/btc_swing  ($BTC_DATA_DIR)
  btc/forward/bybit/BTCUSDT/<day>/<HH>.jsonl[.gz]   immutable raw events
  btc/forward/bybit/BTCUSDT/state.json              collector state (last order-book update ids, stats)
  btc/forward/seed/days/*.parquet, raw/*.csv.gz, seed_manifest.jsonl   archive warm-up
  btc/forward/derived/forward_5m/<day>.parquet      completed 5m rows (pure function of raw)
  btc/forward/derived/processor_state.json          processor offsets (file/line, relative to the raw dir)
  btc/forward/signals/signals.jsonl, outcomes.jsonl append-only journals
  btc/forward/paper/paper_trades.jsonl, paper_state.json
  btc/forward/logs/forward_run.log, cycles.jsonl, collector_stats_*.json
  btc/forward/forward_run.pid                       written by the running process
/etc/btc-v5-forward.env, /etc/systemd/system/btc-v5-forward*.{service,timer}, /etc/logrotate.d/btc-v5-forward
```

## 3. Install (target host)
```
sudo bash deploy/install.sh /opt/btc_swing /var/lib/btc_swing     # packages, service user, uv, venv, units, logrotate, journald
cd /opt/btc_swing && git log --oneline -1                          # must be the SAME commit as the source host
```
`install.sh` enables the units but does not start them: the state is migrated first.

## 4. What must be copied (exactly)
Everything under `$BTC_DATA_DIR/btc/forward/` except `forward_run.pid`:
| directory / file | why | migration rule |
|---|---|---|
| `bybit/BTCUSDT/<day>/*.jsonl[.gz]` | raw events (source of truth; bars are rebuilt from them) | copy byte-identical; verify sha256 of every closed `.gz`; never edit |
| `bybit/BTCUSDT/state.json` | collector state: `last_orderbook_u` per topic, last stats, `saved_at` | copy; the new process continues the gap detection from it |
| `seed/days/*.parquet`, `seed/seed_manifest.jsonl` | warm-up (fixed for the whole observation) | copy; must stay identical (integrity check). `seed/raw/*.csv.gz` (2.8 GB) are NOT in the bundle by default (`export --include-seed-raw` adds them): re-downloadable from the public archive, sha256 in `seed_manifest.jsonl` |
| `derived/forward_5m/*.parquet` | completed 5m rows | copy (or rebuild from raw: identical by construction) |
| `derived/processor_state.json` | offsets: `file` (relative `<day>/<HH>.jsonl`), `line`, `last_bar_close_ms`, `replay_sha`, ticker state | copy; processing resumes at exactly this line |
| `signals/signals.jsonl`, `signals/outcomes.jsonl` | prospective snapshots and matured outcomes (append-only) | copy; earlier lines must remain byte-identical |
| `paper/paper_trades.jsonl`, `paper/paper_state.json` | virtual ledger (append-only closed trades; state) | copy |
| `logs/cycles.jsonl`, `logs/forward_run.log`, `logs/collector_stats_*.json` | operational history | copy |
| `manifests/v5_forward_freeze.json` (repo) | the freeze | identical in the checkout; `verify` fails otherwise |
| `config/btc_swing_v5.yaml`, `config/btc_swing_v5_forward.yaml` (repo) | frozen strategy / ops config | identical in the checkout |
Nothing else is needed: the historical research datasets (`data/btc/datasets`, 625 MB) are not used by the forward mode.

## 5. Migration procedure (cold, restart-safe)
Source host (this container or any previous host):
```
bash deploy/migrate_export.sh [out_dir]
```
This stops the runner gracefully (SIGTERM; the collector flushes and saves `state.json`, the cycle
loop finishes), writes `integrity_before.json` (counts, uniqueness, hashes of every artefact),
then `v5_forward_state.tar.gz` + `v5_forward_state.manifest.json` (sha256 and size of every file,
embedded integrity snapshot, freeze hash, observation start) + `v5_forward_state.tar.gz.sha256`.
Copy the four files to the target, e.g. `rsync -av out_dir/ target:/var/lib/btc_swing/import/`.

Target host:
```
sudo bash deploy/migrate_import.sh /var/lib/btc_swing/import /opt/btc_swing /var/lib/btc_swing
```
which (1) checks the archive checksum, (2) extracts into `$BTC_DATA_DIR/btc/forward` (refuses to
overwrite an existing observation), (3) compares the bundled freeze manifest and frozen V5 config
with the checkout, (4) `btc-swing v5 forward verify` re-hashes every migrated file against the
manifest and checks the freeze hash, (5) runs ONE cycle: the processor resumes from the migrated
offsets (raw file keys are relative to the raw directory, so the host path does not matter),
appends nothing that already exists, (6) `btc-swing v5 forward integrity --compare
integrity_before.json` asserts: same freeze hash and observation start; signals/outcomes/paper
trades/cycles counts non-decreasing with the earlier lines byte-identical (prefix hash) and no
duplicates; outcomes reference known signals; bars unique by open time and non-decreasing with
earlier partitions unchanged; processor offset not behind; every closed raw file byte-identical;
seed identical; collector order-book ids carried, (7) starts the service and the health timer and
prints the status.

The gap between the source stop and the target start (minutes) is a collector gap: the runner
carries it as flagged zero-volume bars (previous close) exactly like any reconnect gap; it is
visible in the daily snapshot (`gaps`, `gap_filled`) and in signals' `after_gap_12_bars` flag.
No bar, signal, outcome or trade is created twice: journals are append-only and keyed
(`signal_id`, trade key `(family, side, entry_ms)`), bars are unique by `open_time_ms`, the
processor replays only the one message recorded in `replay_sha`, and every closed trade re-derived
by the deterministic engine is compared with the stored one (`integrity_errors` in `paper_state.json`).

Hot export (runner running) is accepted only for a rehearsal: the manifest records
`runner_was_alive = true`; the last raw hour file may end with a partial line that the processor
skips, and the target would start from that state while the source keeps writing. For the real
migration stop the source first and never run two runners on the same observation.

## 6. Verification commands
```
sha256sum -c v5_forward_state.tar.gz.sha256                           # archive intact
btc-swing v5 forward verify --manifest .../v5_forward_state.manifest.json   # every file re-hashed, freeze hash checked
btc-swing v5 forward integrity --compare .../integrity_before.json    # continuation proof after the first cycle
btc-swing v5 forward status-text                                      # one-screen status
btc-swing v5 forward health                                           # exit 1 on any problem
sha256sum config/btc_swing_v5.yaml; python3 -c "import json;print(json.load(open('manifests/v5_forward_freeze.json'))['v5_config_hash'])"
uv run btc-swing v5 forward freeze                                    # idempotent: prints the EXISTING record, refuses a changed hash
```
(The config hash in the freeze is the canonical-YAML hash computed by the code, so compare it
with `btc-swing v5 forward freeze`'s output rather than with `sha256sum` of the file.)

## 7. Service operation
- `systemctl status btc-v5-forward`, `journalctl -u btc-v5-forward -f` (persistent journal, 90 days / 2 GB), app log `$BTC_DATA_DIR/btc/forward/logs/forward_run.log` (logrotate daily, 60 rotations, copytruncate).
- Automatic start after reboot (`WantedBy=multi-user.target`), `Restart=always` with `RestartSec=30`, no start-rate limit; graceful stop `KillSignal=SIGTERM`, `TimeoutStopSec=90`.
- Health timer every 5 minutes: `btc-v5-forward-health.timer` -> `health` (runner alive, raw write age <= 120 s, collector heartbeat, last bar <= 15 min, last cycle <= 15 min, free disk >= 5 GB). A failure triggers `btc-v5-forward-alert.service`, which pipes the status text to `ALERT_CMD` (default: syslog; set mail/webhook in the env file).
- Disk: raw data is never deleted; watch `status-text` (free GB, forward data size). The seed can be excluded from backups (reproducible from the archive, sha256 in `seed_manifest.jsonl`).
- Code updates during the observation: only operational code may change; `config/btc_swing_v5.yaml` and `btc_swing/v5/{features,events,engine,stage_a,config}.py` must not. After `git pull` + `uv sync --frozen`: `systemctl restart btc-v5-forward`; if a processor change requires rebuilding bars, delete `derived/forward_5m` and `derived/processor_state.json` (bars are a pure function of raw; journals are unaffected).

## 8. Rollback
Keep the export bundle. If the target misbehaves: stop it, restore `$BTC_DATA_DIR/btc/forward` from the bundle (`migrate_import.sh` refuses to overwrite: remove the directory first), re-run `verify` and `integrity --compare`.
