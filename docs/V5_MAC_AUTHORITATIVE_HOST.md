# BTC V5 — the Mac as temporary authoritative forward + STRATEGY_DEMO host

Owner decision 2026-10-08: until the Windows NUC is ready (about 3 days), the owner's Mac is the
SINGLE authoritative host of the V5 forward observation and, after both gates pass, of STRATEGY_DEMO.
Nothing here changes V5: same freeze (`manifests/v5_forward_freeze.json`, V5 config hash
`d18ebf19bd0c…`), same observation start `2026-10-07T14:59:22.972000+00:00`, no new freeze. Paper /
Bybit DEMO only: production endpoints and real-money orders remain impossible (host allowlist).

## 1. What is enforced in code

| property | where |
|---|---|
| `FORWARD_HOST_PREFLIGHT` accepts LINUX + healthy systemd OR MACOS + healthy launchd (`gui/<uid>/com.btcswing.v5-forward` running); the managed pid must BE the runner; nothing else was relaxed (persistent host, runner on this host, exactly one runner, freeze hash, observation start) | `v5/forward/host.py`, `v5/demo/preflight.py` |
| Single-runner lock: `v5 forward run` takes an exclusive `flock` on `<forward>/forward_run.lock`; a second runner on the same data exits at once | `host.RunnerLock`, `forward/runner.py` |
| Authority lease: append-only hash-chained `<forward>/host/authority.jsonl` (`AUTHORITY_CLAIMED` / `AUTHORITY_RELEASED`, bound to a host id). A runner refuses to start if another host holds the lease or it was released; a copied data directory can never silently become a second authoritative runner | `host.py`, `v5 forward claim-authority / release-authority` |
| STRATEGY_DEMO is switched ONLY by an immutable `STRATEGY_DEMO_ACTIVATED` event (exact UTC timestamp, host id) appended by `v5 demo activate` after EVERY gate passed; `config/btc_swing_v5_demo.yaml` stays `mode: DISABLED` | `v5/demo/activation.py` |
| Triggers whose 5m bar closed at or before the activation timestamp are refused (`PRE_ACTIVATION_SIGNAL_REFUSED`); only the trigger of the bar that just closed is ever considered (no retrospective trades); no signal = no order; one position, no pyramiding/averaging; frozen stops/targets/exits; sizing from the 5,000 USDT virtual reference equity at 0.25% (never from the Demo wallet) | `v5/demo/strategy.py`, `runtime.demo_cycle` |
| Activation is host-bound: after a migration the NUC must activate again | `activation.effective_activation` |
| Missing forward data is reported, never backfilled: archive (seed) days are warm-up only and never on/after the observation start day; collector outages are zero-trade rows flagged `gap_filled` | `v5 forward coverage`, `pipeline.extend_seed/assemble` |
| Demo journals and the authority lease migrate with the state; integrity comparison proves their prefix identity | `ops.STATE_DIRS`, `integrity_compare` |

## 2. Forward data present before the Mac (previous collector = ephemeral cloud container)

`btc-swing v5 forward coverage` on the cloud state at 2026-10-08 21:24 UTC:

- 343 forward 5m rows from 2026-10-07 14:55, last completed bar 2026-10-08 19:30 UTC; only **17
  live bars** (the collector was down most of the time). 0 signals, 0 paper trades, 0 duplicates.
- Missing (flagged, never backfilled): 2026-10-07 15:30→15:55 (5 bars), 2026-10-07 16:20 →
  2026-10-08 19:05 (321 bars), and everything from 2026-10-08 19:30 until the Mac's collector
  writes its first message (the import + first cycle turn this into flagged zero-trade rows; the
  coverage report lists it).
- That state was cold-exported (runner stopped), the old collector's authority was released
  (`AUTHORITY_RELEASED`, legacy), and the bundle (tar.gz + manifest + sha256 + integrity snapshot)
  is on the branch `v5-forward-state-cloud-20261008` of this repository (data only, not code). The
  cloud container must never run the forward runner again (its lease is released: it refuses).

## 3. Mac setup (run in order; STOP at the first failure and report it)

Assumptions, stated plainly: the Mac stays **plugged into power with the lid OPEN** (closing the lid
sleeps a MacBook regardless of any assertion, unless it is in clamshell mode with an external
display, keyboard and power), the owner stays **logged in** (LaunchAgents run in the login session;
after a reboot they start again at login), Wi-Fi/Ethernet stays connected, and nothing lives in
iCloud-synced folders. Repository at `~/btc_swing`, authoritative data at `~/btc_swing_data`.

```bash
# 0) code
cd ~/btc_swing && git checkout main && git pull origin main && uv sync --all-extras
export BTC_DATA_DIR="$HOME/btc_swing_data"       # every manual command below uses this

# 1) no other forward runner anywhere: nothing on this Mac ...
pgrep -fl "btc-swing v5 forward run" || echo "no runner on this Mac"
#    ... and the cloud collector is stopped with its authority released (section 2)

# 2) install LaunchAgents (rendered, not loaded) and the data directory
bash deploy/macos/install_macos.sh "$HOME/btc_swing_data"

# 3) demo credentials for the runner (mode 600; values are never printed)
umask 077; printf 'BYBIT_DEMO_API_KEY=%s\nBYBIT_DEMO_API_SECRET=%s\n' \
  "$BYBIT_DEMO_API_KEY" "$BYBIT_DEMO_API_SECRET" > ~/.config/btc_swing/demo.env
chmod 600 ~/.config/btc_swing/demo.env

# 4) import the previous collector's state (verifies sha256 of the bundle and of every file,
#    freeze and V5 config identity, integrity/prefix identity; then prints the coverage)
git fetch origin v5-forward-state-cloud-20261008
mkdir -p ~/btc_swing_import && git archive FETCH_HEAD | tar -x -C ~/btc_swing_import
bash deploy/macos/import_state_macos.sh ~/btc_swing_import "$HOME/btc_swing_data"

# 5) tests before anything starts
uv run pytest -q

# 6) claim authority + start (launchd: auto-start at login, restart on exit, SIGTERM stop,
#    logs in ~/btc_swing_data/btc/forward/logs, health check every 5 minutes, caffeinate held)
bash deploy/macos/start_macos.sh "Mac = temporary authoritative host until the NUC (owner 2026-10-08)"

# 7) wait for the first live bar (~5-10 min), then:
uv run btc-swing v5 forward host-status

# 8) both gates, fresh, on this Mac (mode stays DISABLED)
uv run btc-swing v5 demo preflight     # needs DEMO_EXECUTION_PREFLIGHT=PASSED and FORWARD_HOST_PREFLIGHT=PASSED

# 9) ONLY if both PASSED: activate (re-runs the full test suite and both gates, checks the PASSED
#    smoke, freeze, observation start, integrity, reconciliation and sizing; writes nothing unless
#    every gate passes)
uv run btc-swing v5 demo activate --note "owner: STRATEGY_DEMO on the Mac, 5000 USDT virtual, 0.25%"

# 10) status (add --live for the signed GET-only read of the Demo position and open orders)
uv run btc-swing v5 forward host-status --live
```

If step 4 cannot be used (bundle unavailable), start without it: the Mac then has no forward bars
before its own collector, the whole period since the observation start is reported missing by
`v5 forward coverage`, and the frozen indicators need the archive warm-up plus enough live bars;
archive data is never used after the observation start.

The runner picks up an activation at its next 5-minute cycle (no restart needed). The first 12 bars
after any collector gap are blocked for Demo entries (`DATA_GAP_IN_FEATURE_WINDOW`).

## 4. Sleep safety (minimum change, 3 days)

- The runner wrapper holds `caffeinate -i -m -s -w <runner pid>` for exactly the lifetime of the
  runner: no idle sleep, no system sleep while on AC power, no disk idle sleep. The display may
  sleep. Nothing is changed permanently. Check: `pmset -g assertions | grep -i caffeinate`.
- Belt and braces while plugged in (optional, reversible): record the current values with
  `pmset -g custom`, then `sudo pmset -c sleep 0 disksleep 0` (AC profile only; the battery
  profile is untouched). Revert after the migration with the recorded values, e.g.
  `sudo pmset -c sleep 1 disksleep 10`.
- System Settings: Battery → Options → "Prevent automatic sleeping on power adapter when the display
  is off" ON (same as the pmset line), Low Power Mode OFF; Software Update → automatic install of
  macOS updates OFF for these 3 days (an update restart stops the runner until the next login).
- The Mac must stay on power: on battery `caffeinate -s` has no effect and macOS may sleep.

## 5. Operations

```bash
export BTC_DATA_DIR="$HOME/btc_swing_data"; cd ~/btc_swing
uv run btc-swing v5 forward host-status [--live]           # everything in one screen
launchctl print gui/$(id -u)/com.btcswing.v5-forward | grep -E "state|pid|runs|last exit"
tail -f ~/btc_swing_data/btc/forward/logs/forward_run.log  # app log; launchd.*.log for stdout/err
tail ~/btc_swing_data/btc/forward/logs/health.log          # 5-minute health results
uv run btc-swing v5 forward coverage                       # missing periods, duplicates
uv run btc-swing v5 forward signal-diagnostic              # READ-ONLY: why no signal fires now (--json)
bash deploy/macos/stop_macos.sh                            # graceful stop (SIGTERM)
bash deploy/macos/start_macos.sh "restart"                 # start again (lease already held)
uv run btc-swing v5 demo deactivate --note "..."           # STRATEGY_DEMO off (only when flat)
```

## 6. Cold migration Mac → Windows NUC (WSL2), in ~3 days — NOT today

NUC prerequisites: Windows power plan never sleeps on AC, Windows Update active hours / pause
updates for the observation, WSL2 Ubuntu with systemd enabled (`/etc/wsl.conf`: `[boot]
systemd=true`, then `wsl --shutdown`), WSL kept running (a systemd service keeps the VM alive; do
not run `wsl --shutdown`), data in the Linux filesystem (`/var/lib/btc_swing`, never `/mnt/c`), and
`sudo bash deploy/install.sh` done (repository at the same commit as the Mac).

1. Mac — wait until STRATEGY_DEMO is flat (`v5 demo status`: no open strategy position, no
   reconciliation pending); optionally `v5 demo deactivate --note "migration"`.
2. Mac — `bash deploy/macos/migrate_export_macos.sh ~/btc_swing_export`: graceful stop (SIGTERM),
   LaunchAgents disabled, authority RELEASED (part of the export), integrity snapshot, complete
   export (raw, collector state, offsets, bars, signals, outcomes, paper ledger, logs, demo
   journals/state/trades, authority lease) and its sha256. From here the Mac cannot run as
   authoritative host (lease released; agents disabled).
3. Copy the four files (`v5_forward_state.tar.gz`, `.manifest.json`, `.tar.gz.sha256`,
   `integrity_before.json`) to the NUC, e.g. `scp ~/btc_swing_export/* nuc:/var/lib/btc_swing/import/`.
4. NUC — `sudo bash deploy/migrate_import.sh /var/lib/btc_swing/import`: checksum, extraction,
   freeze/V5 config identity, every file re-hashed, one cycle, integrity comparison (journal prefix
   identity for signals/outcomes/paper/cycles and the hash-chained demo/authority journals, no
   duplicate bars/signals/paper or demo trades, offsets not behind, closed raw files and seed
   byte-identical), then `claim-authority` and `systemctl start`.
5. NUC — `btc-swing v5 forward coverage` (the Mac→NUC transfer window is reported missing, never
   backfilled), `btc-swing v5 demo preflight` (both gates), then the owner runs
   `btc-swing v5 demo activate --note ...` on the NUC (activation is host-bound).
6. Mac — after the NUC is confirmed: `launchctl bootout` already done; remove the agents
   (`rm ~/Library/LaunchAgents/com.btcswing.v5-forward*.plist`), revert pmset if changed, and delete
   `~/.config/btc_swing/demo.env`.

Same observation start and freeze throughout; never `v5 forward freeze` on the NUC.
