type:: project
status:: active
tags:: #ais #cyberdeck #rf #sdr #flask
updated:: 2026-06-23

# AIS App

Standalone CyberDeck AIS vessel tracker matching the ADS-B app UI style.

## Runtime

| Label | Value |
|---|---|
| App port | 5410 |
| AIS-catcher JSON TCP | 10111 |
| NMEA UDP input | 10110 |
| Service | ais-app |
| Path | /home/slofi/Projects/ais-app |

## Decoder

The app starts AIS-catcher with RTL-SDR device 0:

```bash
AIS-catcher -d:0 -S 10111 JSON_FULL on -q -gr TUNER 40
```

It also listens for external AIS NMEA on UDP 10110.

## Local Vessel DB

Local database path:

```text
/home/slofi/intercept/data/ais/vessel_db.json
```

The DB is keyed by MMSI and learns from received AIS static messages. It stores vessel name, callsign, IMO, ship type, dimensions, destination, MID/country, and MMSI kind when available.

The Settings panel includes AIS DB status, update from current session, import, export, and clear controls.

## Maps

Online layers match ADS-B. Offline layers are read from the shared OPS-TOC/mbtileserver map database on port 8092; OPS-TOC remains the app used to download/manage map tiles.

## State

| **Repo** | `github.com:Slofi/AIS-app.git` (main) |
| **Latest commit** | `1d16d33` |
| **GPS source** | OPS-TOC proxy (`localhost:8090/api/gps`) — config at `/home/slofi/intercept/data/ais/receiver_config.json` |

## Changelog

- **2026-06-23** — Styled full-page shutdown/restart splash: big "AIS" in accent, restart command, Dashboard hint. Commit `1d16d33`
- **2026-06-23** — GPS source changed from `om` to `opstoc` in `receiver_config.json` (OM not running on CD)
- **2026-06-19** — Cache-busting for static assets on updates. Range ring labels and direction indicators. Commit `6cae50e`, `1786c2a`
- **2026-06-19** — Custom `_confirmAction()` popup for shutdown/restart. Restart/shutdown UX matched to ADS-B pattern. Commits `b24a942`, `2b7690a`
- **2026-06-19** — Initial build: AIS-catcher decoder, vessel tracking, Leaflet map, vessel list, local DB, GPS/position sources. Commit `3d7b3f8`
