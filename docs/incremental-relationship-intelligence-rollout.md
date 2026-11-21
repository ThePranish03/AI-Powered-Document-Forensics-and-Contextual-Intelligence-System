# Incremental relationship intelligence: future rollout procedure

This document describes separate future operations. They have **not** been run as part of implementation or validation.

## Migration procedure (requires a separate approval)

1. Confirm the target environment and its current Alembic revision using the approved operator credentials.
2. Take and verify the environment's normal database backup. Do not rely on a workspace checkpoint as a database backup.
3. Review the generated SQL for revision `0015_incremental_relationship_intelligence` against the target database dialect.
4. With explicit approval for that target, run `alembic upgrade 0015_incremental_relationship_intelligence` using that environment's approved configuration.
5. Verify the schema contains the two new tables and their indexes. Do not run the downgrade as a validation step; it drops these tables.

## Historical evidence and candidate backfill (requires separate approval)

Backfill is intentionally not part of revision 0015. Before any future backfill:

1. Approve a separate, resumable, dry-run-capable job and its target investigation/document scope.
2. Read only already persisted OCR/native evidence blocks; do not open original uploads or invoke OCR/provider services.
3. Reuse the source-occurrence uniqueness key and `MATCHER_VERSION`; report counts and collisions in dry-run mode.
4. Preserve every existing `DerivedGraphNode` and review record. Map legacy review decisions onto stable candidates only when the legacy identifier and investigation match unambiguously; record the mapping without deleting or rewriting the source review record.
5. Run the approved scope in bounded batches, then verify counts, provenance, candidate lifecycle, and review mappings before exposing results.

No database migration, backfill, production-data access, or upload access was performed while preparing this procedure.
