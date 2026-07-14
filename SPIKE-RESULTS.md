# Resultados do Spike — viabilidade PROVADA (2026-07-14)

O estudo de `VIABILIDADE-MCP-PoB2.md` foi validado empiricamente. Nada mais é suposição.

## O que foi provado

1. **Engine headless boota no container.** Usamos a imagem **pré-construída**
   `ghcr.io/pathofbuildingcommunity/pathofbuilding-tests:latest` (não precisa
   buildar o Dockerfile pesado — Lua 5.1.5 + LuaJIT + busted já vêm prontos).
2. **Specs de sistema passam:** `spec/System/TestDefence_spec.lua` → 18/18 ok.
3. **Dirigimos o engine por conta própria** com `src/mcp_spike.lua`:
   `dofile("HeadlessWrapper.lua")` → `newBuild()` → set `customMods` →
   `runCallback("OnFrame")` → ler `build.calcsTab.mainOutput.*`.

Saída real do spike (build vazio + "100% increased maximum Mana"):
`Life=65, Mana=98, EnergyShield=0, TotalEHP=47.76, FireResistTotal=-50`.

## Pendências de verificação — RESOLVIDAS

- **Share code** (fonte: `src/Classes/ImportTab.lua:133` e o import handler):
  `base64( zlib_deflate(XML) )` + `+`→`-`, `/`→`_` (base64url, **sem** header/prefixo).
  Decodificador Python pronto e testado: `server/pob_mcp/share_code.py` (8/8 testes).
- **Campos de saída PoB2** (fonte: `CalcDefence.lua`, `TestDefence_spec.lua`):
  - EHP: `mainOutput.TotalEHP`, `mainOutput.EHPSurvivalTime`
  - Max hit/tipo: `calcsOutput.{Physical,Fire,Cold,Lightning,Chaos}MaximumHitTaken`
  - Pools: `Life`, `Mana`, `EnergyShield`, `Ward`
  - Resist: `{Fire,Cold,Lightning,Chaos}ResistTotal` (+ `...ResistOverCap`)
  - Outros: `Armour`, `Evasion`, `BlockChance`

## Invocação do container (Git Bash no Windows)

Precisa de `MSYS_NO_PATHCONV=1` senão o Git Bash quebra os paths Unix do docker.

```bash
# rodar um spec (cwd vira src via .busted; path do spec é relativo a src => ../spec/...)
MSYS_NO_PATHCONV=1 docker run --rm -e HOME=/tmp -w /workdir \
  -v "/c/desenv/particular/poe2 - path of building MCP/fork:/workdir:ro" \
  ghcr.io/pathofbuildingcommunity/pathofbuilding-tests:latest \
  busted --lua=luajit ../spec/System/TestDefence_spec.lua

# rodar um script Lua nosso (cwd=src, runtime no LUA_PATH, CI=true evita ModCache)
MSYS_NO_PATHCONV=1 docker run --rm -e HOME=/tmp -e CI=true -w /workdir/src \
  -e LUA_PATH="../runtime/lua/?.lua;../runtime/lua/?/init.lua;;" \
  -v "/c/desenv/particular/poe2 - path of building MCP/fork:/workdir:ro" \
  ghcr.io/pathofbuildingcommunity/pathofbuilding-tests:latest \
  luajit mcp_spike.lua
```

Repo clonado em `fork/` (blobless, checkout completo ~756M; branch `dev`).

## Entrypoint headless + servidor MCP — FEITOS

- `fork/src/mcp_entry.lua`: engine persistente, protocolo JSON linha-a-linha em
  stdin/stdout. Handlers: `ping`, `import_build`, `calc_stats`, `get_defenses`,
  `set_config`. `print` redirecionado pro stderr → stdout 100% JSON.
- `server/pob_mcp/`: `engine.py` (subprocess Docker + drenagem de stderr +
  request/response), `server.py` (FastMCP: `import_build`, `calc_stats`,
  `get_defenses`, `set_config`, `compare_builds`), `share_code.py` (decoder).
- Testes: `tests/` — 15/15 (8 share code + 7 integração no engine). Boot ~6 s.

### Gotcha crítico do recalc (custou horas)

Depois de `loadBuildFromXML`, aplicar `customMods` + `configTab:BuildModList()` +
`runCallback("OnFrame")` **NÃO** atualiza o `mainOutput` — o output fica cacheado
e o mod (mesmo parseado no `configTab.modList`) nunca aparece. Num build criado
por `newBuild()` funciona; num importado, não.

**Solução:** no recalc, chamar **`build.calcsTab:BuildOutput()` diretamente**
(não confiar no `OnFrame`/buildFlag), e `wipeGlobalCache()` antes (como o
`newBuild` faz) pra invalidar o cache de DPS por skill. Ver `recalc()` no
`mcp_entry.lua`.

## Próximo passo

- `compare_builds` já existe no servidor (import A/B + diff), mas ainda não foi
  exercitado com dois share codes reais — validar quando tivermos códigos de teste.
- Import de conta/trade (precisa de rede/stub) — escopo posterior.
- Empacotar: `docker-compose` de produção + config MCP no Claude.
