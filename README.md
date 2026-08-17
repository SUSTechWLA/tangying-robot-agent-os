# Tangying Robot Agent OS

Distributed Agent control plane and edge runtime for embodied robots. The v0.1 target is a natural-language tabletop pick-and-place loop shared by a headless simulator and XLeRobot.

The safety contract is strict: cloud models issue high-level skills only; the Raspberry Pi Robot Edge owns hardware validation, command leases, cancellation, and stop behavior.

## Development

```bash
make setup
make generate
make test
```

See `docs/quickstart.md` and `docs/safety-checklist.md` before connecting physical hardware.
