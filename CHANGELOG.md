# Changelog

All notable changes to MyFans Downloader are documented in this file.

## [1.0.0] - 2026-08-15

### Added

- Native Windows desktop application based on WebView2 and pywebview.
- Single-file Windows executable with embedded UI, FFmpeg, and FFprobe.
- Windows DPAPI protection for the locally stored authorization token.
- SQLite completion tracking, bounded progress events, cancellation, and
  single-instance protection.
- Reproducible PyInstaller build definition, multi-resolution application icon,
  and SHA-256 release checksum generation.

### Changed

- Reorganized the project into a standard `src/myfans_downloader` package.
- Centralized MyFans API endpoints, authentication headers, retry policy, and
  timeouts.
- Replaced the browser-hosted Flask application with a local file UI connected
  exclusively through the native JavaScript bridge.
- Moved writable runtime data outside the executable and source package.
- Aligned the wider desktop sidebar, brand padding, navigation baseline, and
  responsive content layout to prevent horizontal overflow.

### Security

- Removed all localhost hosting and browser fallback paths.
- Added a restrictive Content Security Policy with `connect-src 'none'`.
- Removed tokens from process-environment configuration and added log redaction
  for credentials and signed URL query strings.
- Added archive extraction path validation and FFmpeg checksum verification.

## [0.1.1] - 2026-06-27

- Improved download-state persistence and FFmpeg platform handling.

## [0.1.0] - 2026-06-21

- Initial public release.
