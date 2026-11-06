# tool — specification

## Flags

- `--mode` (default: `fast`) — processing mode.
- `--level` one of `low|high` — detail level.
- `--verbose` — extra logging.
- There is no `--legacy` flag.

## Command: export

Exports the collected data to the output file.

## Requirements

- FR-001 — Export writes the collected records to the output file.
- FR-002 — The mode flag selects the processing speed.

## Endpoints

- `GET /items` — lists the collected items.
- `POST /items` — creates a new item.

## Configuration

The tool reads these configuration keys:

- `server` — server settings.
- `server.host` — bind address.
- `server.port` — bind port.
- `database` — database settings.
- `database.url` — connection string.

The shipped example file config.example.toml declares
exactly those keys:

```toml
[server]
host = "localhost"
port = 8000

[database]
url = "sqlite:///items.db"
```

## Environment variables

- `ITEMS_API_KEY` — API key for the items service.
- `ITEMS_REGION` — deployment region.
- The tool never reads `AWS_*`.
