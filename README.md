# Path of Building 2 — MCP Server

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant
(Claude Code, etc.) **read and optimize Path of Exile 2 builds** using the real
Path of Building 2 calculation engine — headless, no GUI automation.

Point it at a build (a `pobb.in` link or a share code) and ask things like
*"why is my DPS low?"*, *"what's the best support to swap here?"* or *"rank the
runes for this weapon"* — the answers come from the actual PoB2 engine, not a
guess.

```
AI assistant  ⇄  MCP server (Python)  ⇄  mcp_entry.lua (LuaJIT, headless)  ⇄  PoB2 engine
                                          running inside the PoB Docker image
```

## What it can do

| Tool | What it does |
|------|--------------|
| `import_build(code)` | Load a build from a **pobb.in link**, a `/raw` URL, or a share code |
| `calc_stats` / `get_defenses` | DPS, crit, life/ES, resistances, EHP, max hit taken |
| `list_skills` / `list_items` / `get_equipped(slot)` | Inspect gems, gear, and item text |
| `list_runes(slot)` | Valid runes / soul cores for an item |
| `set_config(mods)` | What-if custom modifiers (`"Enemy is Shocked"`, `"+2 gem levels"`, …) |
| `simulate_item(slot, raw)` | Equip a pasted item and report the stat delta |
| `optimize_supports(group?)` | Rank the best support-gem swaps for a skill by DPS gain |
| `optimize_runes(slot)` | Rank every valid rune in a socket by DPS gain |
| `compare_builds(a, b)` | Two builds side by side |
| `poe_login_start` / `poe_login_finish` | Log in to your pathofexile.com account (OAuth) |
| `list_characters` / `import_character(name)` | Import a **live** character straight from your account |

### Live character import (account login)

Instead of exporting to a share code, you can pull a character straight off your
account:

```
> poe_login_start           # returns a URL — open it and approve
> poe_login_finish          # completes the login
> list_characters           # see your PoE2 characters
> import this character: MyWitchhunter
```

This uses the same OAuth (Authorization Code + PKCE) flow Path of Building
itself uses: the browser is sent to `pathofexile.com/oauth/authorize` and
redirected to a local listener that catches the login. Nothing is scraped and
no password touches this tool.

> **Caveat:** it reuses Path of Building's registered OAuth client
> (`client_id=pob`) so the localhost redirect is accepted without registering a
> new app. Treat it as driving the same PoB integration. Tokens live in memory
> for the server's lifetime only (no on-disk persistence). If you don't need
> live import, the `pobb.in` link flow above needs no login at all.

## Requirements

- **Docker** — runs the headless PoB engine (the image is pulled by setup)
- **Python 3.11+**
- **Git** — setup clones the PoB fork
- An **MCP client** (e.g. Claude Code)

## Install

```bash
git clone https://github.com/<you>/poe2-pob-mcp.git
cd poe2-pob-mcp

# Linux / macOS / Git Bash:
./setup.sh
# Windows PowerShell:
./setup.ps1
```

`setup` clones the Path of Building 2 fork **at a pinned commit** into `fork/`
(it is *not* vendored in this repo — see *Licensing*), injects the headless
entrypoint `engine/mcp_entry.lua`, pulls the Docker image, and creates a Python
virtualenv in `.venv/` with the deps installed. The venv avoids the common
"No module named pip" / PEP 668 breakages on modern Linux.

> On Debian/Ubuntu, creating a venv needs the `python3-venv` package:
> `sudo apt install python3-venv` (setup prints this if it's missing).

## Configure your MCP client

Copy the example config and set the absolute path to where you cloned the repo:

```bash
cp .mcp.json.example .mcp.json
# edit .mcp.json: replace <ABSOLUTE_PATH_TO_REPO> with your clone path.
# Windows: use .venv/Scripts/python.exe instead of .venv/bin/python
```

Then restart the client and confirm the server is up (in Claude Code: `/mcp`).
The first engine call boots the Docker container and takes ~6 s; after that the
process stays hot.

## Usage

```
> import this build: https://pobb.in/XXXXXXXX
> what are my resistances and DPS?
> optimize the supports on my main skill
```

## Project layout

```
server/pob_mcp/     Python MCP server (FastMCP)
  ├─ server.py        tool definitions
  ├─ engine.py        persistent Docker engine process (JSON over stdin/stdout)
  ├─ optimizer.py     save-mutate-measure-restore simulations & scan-rank
  ├─ share_code.py    decode/encode PoB2 share codes (base64 + zlib)
  └─ fetch.py         resolve a pobb.in / pastebin link to a share code
engine/mcp_entry.lua  headless entrypoint injected into the fork by setup
tests/                unit tests (+ Docker integration tests, auto-skipped)
setup.sh / setup.ps1  clone the pinned fork + inject entrypoint + pull image
```

## Tests

```bash
python -m pytest tests -q     # unit tests; integration tests skip without Docker
```

## Licensing

This project is MIT (see [LICENSE](LICENSE)). It does **not** redistribute Path
of Building — `setup` clones it from the upstream repository
([PathOfBuildingCommunity/PathOfBuilding-PoE2](https://github.com/PathOfBuildingCommunity/PathOfBuilding-PoE2),
also MIT) at install time. The only PoB-adjacent file shipped here is
`engine/mcp_entry.lua`, an original headless entrypoint.

Path of Exile is a trademark of Grinding Gear Games. This project is not
affiliated with or endorsed by Grinding Gear Games or the Path of Building
Community.

## Notes & limitations

- **No network inside the engine** — account/trade import isn't supported; the
  server fetches `pobb.in` links itself (in Python) and hands raw XML to the engine.
- **Trigger / detonator skills** (grenades, herald explosions) don't get a
  meaningful headless DPS — optimize those by mechanic, not by the DPS number.
