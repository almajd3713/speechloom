# Compatibility fixtures

These files freeze Speechloom's public version 1 CLI and job-manifest contracts before
the modular application refactor. Treat existing fixtures as immutable. When a public
contract changes compatibly, add a new versioned fixture instead of rewriting the old
one.

The CLI snapshots are captured at an 80-column terminal width. Manifest fixtures cover
both a completed translated job and an interrupted resumable job.
