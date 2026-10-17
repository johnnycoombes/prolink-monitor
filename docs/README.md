# Prolink Listener documentation

Documentation for **prolink-monitor** (desktop app: **Prolink Listener**), version 1.0.23.

| Document | For | What it covers |
|---|---|---|
| [User guide](user-guide.md) | DJs, streamers, booth techs | Install, connecting, every page, web panel, OBS overlay, What's Now Playing, updates, troubleshooting, FAQ |
| [Architecture](architecture.md) | Contributors | Modules, threads, data flow, event stream, caching, updates, build and CI, tests |
| [Protocol notes](protocol.md) | Contributors, protocol nerds | Pro DJ Link as this app implements it: ports, packets, offsets, device numbers, dbserver, NFS, ANLZ, model profiles |
| [HTTP API](api.md) | Integrators | Every endpoint the built-in web server answers, with parameters and example responses |
| [Changelog](../CHANGELOG.md) | Everyone | Per-version changes |

**Read-only by design.** The app never writes to a player, a USB stick, or any rekordbox database (`export.pdb`, `exportLibrary.db`, `master.db`). It announces itself on the link and asks read-only questions (dbserver menus, NFS reads). A packet capture only records what already arrived.

Screenshots referenced from these pages live in [`doc/`](../doc) (the original folder; kept as is).
