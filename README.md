# Polestar (PCCS) — Home Assistant integration

Home Assistant custom integration for Polestar vehicles, talking to the
Polestar Connected Car Service (PCCS) gRPC backend used by the official
Polestar Explore Android app.

This is **not** the GraphQL-based community Polestar integration. PCCS is a
separate backend (`api.pccs-prod.plstr.io` → CNEPMOB → C3 gRPC) that exposes
per-second-grade charging telemetry, lock/door/window state, climatization,
availability, and last-parked location.

## Entities

Single device per VIN. All entities are derived from the proto responses on
the coordinator, so a failed sub-call leaves only its entities `unknown`
while the rest keep updating.

**Sensors** — battery level, range, time-to-full, charging power, charging
status, cabin temperature.

**Binary sensors** — plugged in, charging, locked, any door open, any window
open, tailgate open, hood open, online, in use.

**Device tracker** — GPS location, preferring the live last-known fix and
falling back to the last-parked fix when the car is offline. Exposes
`parked_stale` as a state attribute.

## Setup

1. **Install** — add this repo as a custom HACS repository (category:
   Integration), install **Polestar (PCCS)**, and restart Home Assistant.
2. **Add the integration** — Settings → Devices & Services → Add Integration
   → "Polestar (PCCS)". Enter the 17-character VIN.
3. **Sign in** — the flow renders a Polestar ID authorization URL. Open it
   in a browser, complete login, and paste back the
   `polestar-explore://explore.polestar.com/...` URL the browser fails to
   redirect to. The flow extracts the auth code, exchanges it for tokens,
   and creates the entry.

The polling interval defaults to 300 s and can be changed in the entry's
options (floor: 10 s). 10 s is intentionally aggressive headroom for
watching a charging session in real time; the default is fine for everyday
use.

## Provenance

All endpoints, OAuth parameters, and protobuf schemas are hand-derived from
the decompiled Polestar Explore APK (`com.polestar.explore`, v5.6.1). No
private keys are used; the OAuth `client_id` (`lp8dyrd_10`), issuer
(`polestarid.eu.polestar.com`), and PCCS host are the same ones shipped
publicly in the app.

## Development

```bash
scripts/setup     # install dev deps
scripts/develop   # run HA with this integration loaded
scripts/lint      # ruff
```

`scripts/develop` starts a Home Assistant instance with this repo's
`custom_components/polestar_pccs/` mounted and `config/configuration.yaml`
applied. Generated proto stubs live under
`custom_components/polestar_pccs/proto_gen/` and are imported via an
absolute-path shim in `__init__.py`.

## Credits

Built on the [`integration_blueprint`](https://github.com/ludeeus/integration_blueprint)
template by @ludeeus.
