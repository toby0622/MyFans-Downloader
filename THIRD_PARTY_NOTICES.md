# Third-party notices

MyFans Downloader is distributed under the MIT License. The standalone Windows
executable also contains or invokes third-party components under their own
licenses.

## Runtime components

| Component | Pinned version | License |
| --- | ---: | --- |
| CPython | 3.13 | Python Software Foundation License |
| requests | 2.34.2 | Apache-2.0 |
| tqdm | 4.70.0 | MPL-2.0 AND MIT |
| m3u8 | 6.0.0 | MIT |
| urllib3 | 2.7.0 | MIT |
| pywebview | 6.2.1 | BSD-3-Clause |
| FFmpeg / FFprobe | BtbN LGPL build | LGPL-2.1-or-later |

Transitive Python dependencies required by the packages above retain their own
licenses. The release includes `THIRD_PARTY_LICENSES.zip`, generated from the
exact Python environment used to build the executable, containing the available
license and notice files for all installed build and runtime distributions.

## FFmpeg

The executable includes separate FFmpeg and FFprobe programs from
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds). The build script
selects the LGPL variant, verifies its archive against the publisher's SHA-256
manifest, and includes `COPYING.LGPLv2.1` in both the executable and release
license archive.

FFmpeg source and build information are available from:

- [FFmpeg](https://ffmpeg.org/)
- [FFmpeg source](https://github.com/FFmpeg/FFmpeg)
- [BtbN FFmpeg build scripts](https://github.com/BtbN/FFmpeg-Builds)

FFmpeg is invoked as a separate executable and is not linked into the Python
application.

## Build tools

PyInstaller and Pillow are used to construct the Windows executable and icon.
They are not application APIs. Their license files are also included in the
generated release license archive.

The pinned direct dependency versions are defined in `requirements.txt` and
`requirements-build.txt`.
