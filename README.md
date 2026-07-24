# lit2db-marketplace

A Claude Code plugin marketplace hosting **lit2db** — a domain-agnostic pipeline
for building auditable, versioned, ML-ready databases from scientific literature
and structured data.

## Install

```
/plugin marketplace add ColdMountain24/lit2db-marketplace
/plugin install lit2db@lit2db-marketplace
```

Local development (from a checkout of this repo):

```
/plugin marketplace add ./          # or the path to this repo
/plugin install lit2db@lit2db-marketplace
```

## What's here

| Path | What it is |
|---|---|
| `.claude-plugin/marketplace.json` | Marketplace manifest (this repo's root). |
| `plugins/lit2db/` | The lit2db plugin — agents, hooks, MCP verify/route/gate spine, the scope-elicitation skill, slash commands, and the Pydantic contracts. |

See [`plugins/lit2db/README.md`](plugins/lit2db/README.md) for the full component
tour, the design thesis (verification, not extraction, is the hard problem), and
the offline demo.

## License

MIT — see [`LICENSE`](LICENSE). The scaffold is domain-blind; any domain substance
and redistributed source material are governed by your own project's licensing.
