# Migration and releases

The maintainer develops under the private vault's `_System`, then exports reviewed files into a separate Git working copy. GitHub never overwrites those local sources automatically. The local export manifest includes exact paths; adding a source requires explicit review.

From the private `_System` directory:

```sh
python3 publish/export_public_release.py --output /absolute/path/to/public-working-copy
python3 publish/export_public_release.py --output /absolute/path/to/public-working-copy --apply
```

The first command previews path/hash changes. The second validates all files before writing. The private `.export-receipt.json` records file ownership and is Git-ignored. Local edits in the public working copy block subsequent exports; bring reviewed contributions back into the local source first. Never reset those edits blindly.

For an existing legacy installation, back up the vault separately, stop active coding/document work, review the export, and run:

```sh
./install.sh --vault "$HOME/Documents/SecondBrain" --adopt-existing
```

This explicitly adopts the previous runtime/profile/LaunchAgent paths. The installer backs up replaced files privately; Qdrant, team state, document queues and audit are not migrated or reindexed. Indexing is an explicit command after setup. Installation uses one lock; services are stopped during deployment and previous loaded jobs are restored on failure. Dependency environments are staged before configuration changes and retained for recovery.

For updates from a clean public clone, select the desired tag and rerun `./install.sh --vault <vault>`. The global installation ledger preserves initial backups across updates. Uninstall restores unchanged managed files to their pre-install state and leaves user data and private dependency environments intact.

Before release: run all tests from a clean environment, inspect the exported/staged diff, scan for secrets and verify native clients and dashboard. Only then push main and tag the version. Do not mark browser or live AI checks passed based solely on unit tests. External contributions are reviewed locally and incorporated into the export source before publishing the next version.
