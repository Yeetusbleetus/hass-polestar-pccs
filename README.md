# Polestar (PCCS) — Home Assistant integration

Home Assistant custom integration for Polestar vehicles, talking to the
Polestar Connected Car Service (PCCS) gRPC backend used by the official
Polestar Explore Android app.

This is **not** the GraphQL-based community Polestar integration. PCCS is a
separate backend (`api.pccs-prod.plstr.io`) that exposes per-second-grade
charging telemetry (power/voltage/current, ETAs, plug status) and last-parked
location via gRPC.

> ⚠️ **Status: scaffolding only.** The integration package is laid out and
> registered, but the API client is still the boilerplate `jsonplaceholder`
> stub. Real PCCS calls will be ported from
> [polestar-mvp](https://github.com/Yeetusbleetus/polestar-mvp) (the
> TypeScript reference client this repo lives next to).

## Provenance

All endpoint, OAuth, and protobuf details are hand-derived from the
decompiled Polestar Explore APK (`com.polestar.explore`, v5.6.1). No private
keys are used; the OAuth `client_id` and PCCS host are the same ones shipped
publicly in the app. See the parent `polestar-mvp` repo for the smali source
references behind every magic value.

## Install (HACS, once published)

1. Add this repo as a custom HACS repository (category: Integration).
2. Install **Polestar (PCCS)**.
3. Restart Home Assistant.
4. Settings → Devices & Services → Add Integration → "Polestar (PCCS)".

## Development

```bash
scripts/setup     # install dev deps
scripts/develop   # run HA with this integration loaded
scripts/lint      # ruff
```

`scripts/develop` starts a Home Assistant instance with this repo's
`custom_components/polestar_pccs/` mounted and `config/configuration.yaml`
applied.

## Credits

Built from the [`integration_blueprint`](https://github.com/ludeeus/integration_blueprint)
template by @ludeeus.
