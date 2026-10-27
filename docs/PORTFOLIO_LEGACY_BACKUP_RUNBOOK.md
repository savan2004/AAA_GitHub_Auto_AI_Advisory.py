# MSK legacy portfolio backup — release gate

**Do not run a PostgreSQL cutover until the real production JSON holdings are
safely exported and independently backed up.** A successful Render deployment
or an offline test does not prove a live backup exists.

1. Arrange a controlled, frozen export of the actual legacy `portfolio_data.json`
   from the authorized Render instance. Do not assume the GitHub repository
   contains it. Stop portfolio writes during the snapshot. Never paste client
   holdings, user IDs, credentials or database URLs into issues, logs or chat.
2. On a trusted operator machine, use a separately durable encrypted storage
   destination, **not another path on Render's ephemeral filesystem**.
   Run `python portfolio_snapshot_backup.py /secure/portfolio_data.json /independent/backup-20261027.json`
   to validate without writing. Review the aggregate position count.
3. Explicitly run the same command with `--apply`. The destination must not
   already exist. The backup is created with owner-only file permissions,
   fsynced, read back byte-for-byte and independently parsed. Record the
   SHA-256 and aggregate count in the private operational record.
4. Independently copy the resulting file to protected off-host storage and
   perform a restore drill into a **new temporary location**. Revalidate
   `load_legacy_holdings`, byte hash and holdings count; confirm secure access
   controls. Never use a production database for a restore drill.
5. Only after the backup and restore drill succeed should the separate
   PostgreSQL migration process be considered. It remains opt-in and requires
   its own real PostgreSQL integration/restart tests and cutover approval.

The backup utility is deliberately manual, dry-run by default, and cannot
access Render production data on its own. If a source is concurrently modified,
its consistency check attempts to fail closed; this is not a substitute for
freezing writes or for a filesystem/database-level point-in-time snapshot.
