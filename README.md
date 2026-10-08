# V5 forward state of the previous (cloud) collector — data only, not code

Cold export (runner stopped) of the forward observation state collected by the ephemeral cloud
container, for import on the owner's Mac (`docs/V5_MAC_AUTHORITATIVE_HOST.md` on `main`, step 4).

- Frozen V5 config hash d18ebf19bd0cde67be1c27683d3e7ad0385d2456a6edaa1fe55146f013096177,
  observation start 2026-10-07T14:59:22.972000+00:00 (unchanged; no new freeze).
- 343 forward 5m rows, last completed bar 2026-10-08T19:30:00Z, only 17 live bars; 0 signals,
  0 paper trades, 0 duplicates. Missing periods (never backfilled): `coverage_at_export.json`.
- The old collector's authority was released (`AUTHORITY_RELEASED`, legacy) before the export, so
  the Mac can claim it; the cloud copy refuses to run a forward runner.
- Files: `v5_forward_state_cloud_20261008.tar.gz` (sha256 in `.sha256`), its per-file manifest,
  `integrity_before.json` (for the prefix-identity comparison after import). Seed archive csv.gz
  files are not included (re-downloadable; their sha256 is in seed_manifest.jsonl).
