# Release process

`stryker-cxx` exposes a versioned CLI and report contract. Releases must preserve
backwards-compatible `stryker-cxx.report.v1` fields or document migrations here.

## Checklist

- Update `CHANGELOG.md`.
- Confirm `stryker-cxx --version` reports the intended version.
- Run the normal test suite.
- Run full local validation: `npm run validate:full-spec`.
- Run package smoke: `npm pack --dry-run`.
- Confirm generated package contents include `python/`, `src/`, `bin/`, `docs/`,
  `fixtures/`, `README.md`, `LICENSE`, and `CHANGELOG.md`.
- Confirm `docs/schemas/` is included in the package.
- Tag as `vX.Y.Z` only after the package smoke and CI are green.
- Publish from a GitHub release using the `publish` workflow. It uses npm
  provenance (`npm publish --provenance --access public`) with `NPM_TOKEN`.
- Use workflow dispatch with `dry_run=true` to exercise the publish path without
  publishing.
- Follow the signing/provenance policy in [`signing.md`](signing.md).

## Compatibility rules

- Native report fields may be added but not removed from `stryker-cxx.report.v1`.
- New native statuses must map to a Mutation Testing Elements status.
- CLI flags may gain aliases, but existing flags should keep their current
  behavior unless a release note calls out the break.
- Marmorkrebs integration changes must land with the matching `stryker-cxx`
  contract change.

## Provenance

The publish workflow grants `id-token: write` and uses npm provenance. Do not
publish from a local terminal for public releases, because that bypasses the
GitHub Actions provenance chain.
