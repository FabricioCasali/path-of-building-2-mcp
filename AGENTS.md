# AGENTS.md — Projeto: MCP para Path of Building 2

Contexto para qualquer agente que trabalhe nesta pasta.

## Objetivo do projeto
Construir um **servidor MCP** que permita ao Claude interagir com o
**Path of Building 2** (build planner de Path of Exile 2), via **fork** do
repositório oficial `PathOfBuildingCommunity/PathOfBuilding-PoE2` (licença MIT).

## Decisões já tomadas (não reabrir sem motivo)
- **NÃO** automatizar a GUI. Usar o **motor headless** do PoB.
- O engine headless já existe: `src/HeadlessWrapper.lua` no repo oficial.
- Camadas: `Claude ⇄ MCP server (Python) ⇄ PoB headless (Lua/LuaJIT, fork)`.
- Rodar dentro do container do `Dockerfile` do próprio PoB (Lua 5.1.5 + LuaJIT + busted).
- Servidor MCP em Python (SDK `mcp` oficial).

## Fatos técnicos essenciais (verificados no fonte, branch `dev`)
- Após `dofile("Launch.lua")` + `runCallback("OnInit")` + `runCallback("OnFrame")`,
  o objeto global **`build`** contém tudo.
- Carregar build: `loadBuildFromXML(xmlText, name)` — recebe **XML cru**.
- Ler stats: `build.calcsTab.mainOutput.<Stat>` (ex.: `TotalDPS`, `Life`, `Mana`,
  `EnergyShield`, `CritChance`) e `build.calcsTab.calcsOutput.<Stat>` (defesa detalhada).
- **Gotcha crítico:** `Deflate`/`Inflate` estão stubados (retornam `""`) no headless.
  O share code do PoB é `base64(zlib-deflate(XML))`. Descomprimir no **Python**
  (stdlib `base64` + `zlib`) e passar XML cru pro Lua.
- **Sem rede** no headless (`require` pula `lcurl.safe`). Import de conta/trade
  fica pra depois. Cálculo offline funciona 100%.
- Performance: manter **um** processo headless persistente (stdin/stdout JSON);
  não dar spawn por chamada — o init do engine é a parte cara.

## Instruções para o agente
- Leia `VIABILIDADE-MCP-PoB2.md` antes de qualquer implementação — é a fonte de verdade.
- Antes de codar de verdade, resolva a lista "Pendências de verificação" do doc.
- Sandbox do Cowork: clone do repo é pesado (dados de jogo). Use
  `git clone --filter=blob:none --no-checkout` + sparse-checkout, ou
  `git show HEAD:<arquivo>` para ler arquivos pontuais sem checkout completo.
- Mudanças no fork devem ser **cirúrgicas**: um entrypoint headless novo
  (`src/mcp_entry.lua`), sem tocar na lógica de cálculo.

## Idioma / tom
Português, direto e objetivo. Código e nomes de arquivo em inglês.
