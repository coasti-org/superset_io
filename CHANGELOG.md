# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

TLDR:
- Bugfixes 0.0.1 (Revision)
- Features 0.1.0 (Minor Version)
- Breaking Changes 1.0.0 (Major Version)

We keep track of the superset version (first part) and our wrapping for coasti (second part, pre-release notation)

## Upcoming

### Fixed

- `copy` now supports ZIP sources and targets.

### Changed

- Renamed the CLI `--force` option to `--yes` in `upload` to be consistent with `copy`.
- Asset sanitzation, selection, skipping, and dependency handling now run through the CLI
  layer for `upload` and `download`; the API layer handles complete asset bundles.
  This keeps all asset modifcation logic self-contained in the copy module.

## 0.2.0 - 2026-09-01

### Added

- `copy` and `download` subcommands now have a `--sanitize` option to create yamls that do not change upon re-downloads (#15, #16)

### Fixed

- Authentication no longer required for help messages, and fixed prompt for default credentials.
- Added confirmation dialog before upload, and a `--force` option to prevent it (#18)

## 0.1.2 - 2026-07-14

### Fixed

- Context object is now typed and works when called through coasti (#14)

## 0.1.1

- Initial Release

