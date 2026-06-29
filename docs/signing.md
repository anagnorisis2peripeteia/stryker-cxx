# Signing and provenance policy

Public `stryker-cxx` releases should be created from GitHub, not from a local
terminal.

## Required release shape

- Tags use `vX.Y.Z`.
- Tags are signed when the maintainer environment supports signed tags.
- Pushing a `vX.Y.Z` tag triggers the `release` workflow.
- Manual release workflow dispatch defaults to `npm publish --dry-run`.
- The `release` workflow publishes with npm provenance when tag-triggered or
  explicitly dispatched with `publish=true`.
- Local publishing is reserved for emergency recovery and must be documented in
  the release notes if it ever happens.

## NPM provenance

The publish workflow uses:

```bash
npm publish --provenance --access public
```

The repository must have an `NPM_TOKEN` secret with publish permission for the
`stryker-cxx` package, exposed to npm as `NODE_AUTH_TOKEN`. The workflow also
requests `id-token: write`, which npm uses for provenance.

## Compatibility expectations

- `stryker-cxx.report.v1` is append-only unless a future major version documents
  a migration.
- Native statuses must continue to project into Mutation Testing Elements.
- Marmorkrebs integration changes should land in tandem with `stryker-cxx`
  contract changes.
