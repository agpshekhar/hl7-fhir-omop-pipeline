# BridgeLink channel

`lab-results-in.xml` is the one channel the pipeline runs in BridgeLink. `labpipe bridgelink deploy` (the `bridgelink-deploy` job in Compose) loads it into the engine and deploys it on every `docker compose up`, so the engine never depends on settings saved only inside its container.

## What it does

| Part | Setting |
| --- | --- |
| Source | TCP Listener on port 6661, MLLP framing (start `0x0B`, end `0x1C 0x0D`), HL7 v2 in and out |
| Destination | Database Writer, "Write to staging", in JavaScript mode: inserts the message as received into `staging.raw_message` with `ON CONFLICT (msh10) DO NOTHING`, so a resend is staged once |
| Response | `Auto-generate (Destinations completed)`: the ACK is built after the staging write. `AA` when the row is written (or was already there), `AE` when the write fails |
| Credentials | Read from the container's environment (`POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, from `.env`). None are stored in the XML |

Validation, plausibility limits, NAKs with reasons and the error queue come in Phase 8.

## Looking inside

The engine has no console of its own (the BridgeLink Web Administrator is a separate install). Its REST API is enough to see what it is doing:

- https://localhost:8443/api/ is the API's own documentation page, where each call can be tried in the browser (log in as admin / admin).
- `curl -sk -u admin:admin -H "X-Requested-With: me" https://localhost:8443/api/channels/5d0c3f8e-7a1b-4c2d-9e4f-0a1b2c3d4e5f/statistics` shows how many messages the channel has received, sent and failed.

## Changing it

This file is the source of truth, not the engine's copy.

1. Edit `lab-results-in.xml` (or edit the channel in the Web Administrator and export it over this file).
2. Redeploy it: `docker compose run --rm bridgelink-deploy`.
3. Run `uv run pytest tests/test_bridgelink_channel.py`. Those tests pin the settings the rest of the pipeline depends on.

The XML records the engine version it was written for, so `docker-compose.yml` and this file change together when the image is upgraded.
