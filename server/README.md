# PoB2 MCP Server

Servidor MCP que expõe o motor de cálculo do **Path of Building 2** (headless,
via Docker) como ferramentas para o Claude.

## Arquitetura

```
Claude ⇄ server (Python, FastMCP) ⇄ mcp_entry.lua (LuaJIT headless no Docker) ⇄ engine PoB2
```

- `pob_mcp/share_code.py` — decodifica o share code do PoB (`base64url(zlib(XML))`).
- `pob_mcp/fetch.py` — resolve um **link** de build (pobb.in / pastebin / URL
  qualquer) baixando o raw aqui no Python (o engine não tem rede).
- `pob_mcp/engine.py` — sobe UM processo headless persistente (Docker) e conversa
  por JSON stdin/stdout. O boot (~6 s) é caro, então o processo fica quente.
- `pob_mcp/optimizer.py` — orquestra as simulações (save-mutate-measure-restore)
  e os otimizadores de varredura.
- `pob_mcp/server.py` — tools MCP (abaixo).
- Entrypoint Lua: `../fork/src/mcp_entry.lua`.

## Tools MCP

Leitura/estado (lembram o build ativo — importe uma vez, use nas próximas):
- `import_build(code|xml)` — carrega e vira o build ativo. `code` aceita o
  **link** (`https://pobb.in/xxxx` — baixa sozinho), a URL `/raw`, ou o próprio
  share code. Passar o link evita corromper o code no copy/paste.
- `calc_stats` / `get_defenses` — números de ataque/defesa.
- `list_skills` — grupos de skill, gemas e qual é o principal.
- `list_items` / `get_equipped(slot)` — gear equipado / texto cru de um item.
- `list_runes(slot)` — runas/soul cores válidas pro item do slot.

What-if (restauram o build depois):
- `set_config(custom_mods)` — aplica mods de config ("+2 gem levels", etc.).
- `simulate_item(slot, raw)` — equipa um item colado e devolve o delta de stats.

Otimizadores (varrem e ranqueiam por ganho de DPS):
- `optimize_supports(group?, top?, include_lineage?)` — melhores trocas de suporte.
- `optimize_runes(slot, rune_index?, top?)` — melhor runa/soul core pro socket.
- `compare_builds(code_a, code_b)` — dois builds lado a lado.

## Pré-requisitos

- Docker com a imagem `ghcr.io/pathofbuildingcommunity/pathofbuilding-tests:latest`
  (`docker pull` uma vez).
- O fork do PoB com checkout completo em `../fork` (precisa de `src/` + `runtime/`
  + dados do jogo para o engine inicializar).
- Python 3.11+ e `pip install -r requirements.txt`.

## Rodar

```bash
python -m pob_mcp.server          # stdio (transporte padrão do Claude Code)
```

## Config MCP (Claude Code)

```json
{
  "mcpServers": {
    "path-of-building-2": {
      "command": "python",
      "args": ["-m", "pob_mcp.server"],
      "cwd": "C:/desenv/particular/poe2 - path of building MCP/server"
    }
  }
}
```

## Testes

```bash
python -m pytest ../tests -q      # unit (share code) + integração (engine em Docker)
```

Os testes de integração são pulados automaticamente se o Docker/imagem não
estiverem disponíveis.
