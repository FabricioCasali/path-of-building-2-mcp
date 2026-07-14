# MCP para Path of Building 2 — Estudo de Viabilidade e Arquitetura

> Investigação feita direto no fonte do repositório oficial
> `PathOfBuildingCommunity/PathOfBuilding-PoE2` (branch `dev`, licença MIT).
> Data: 2026-07-13.

## TL;DR

Sim, é possível — e **não** precisa automatizar a interface gráfica. O próprio
PoB já roda o motor de cálculo **sem GUI** (é assim que ele roda os testes de
regressão no CI). A estratégia certa é: **fork enxuto** que expõe o engine
headless, com um **servidor MCP** por cima traduzindo isso em ferramentas.

```
Claude  ⇄  Servidor MCP (Python)  ⇄  PoB headless (Lua/LuaJIT, seu fork)
```

O fork mexe pouco. Nada de reescrever o PoB.

---

## Por que dá certo: as 3 evidências no código

### 1. Licença MIT
Fork liberado, modificar e distribuir sem drama jurídico.

### 2. O motor roda headless — e isso já existe pronto
O arquivo **`src/HeadlessWrapper.lua`** é a chave de tudo. Ele:

- Substitui (stub) TODAS as funções gráficas e de host que o PoB espera do
  runtime SimpleGraphic: `DrawImage`, `DrawString`, `RenderInit`,
  `GetScreenSize`, `NewImageHandle`, etc. — todas viram no-ops.
- Faz `dofile("Launch.lua")` para inicializar o programa inteiro.
- Roda `runCallback("OnInit")` e um `runCallback("OnFrame")` (precisa de pelo
  menos um frame para tudo inicializar).
- Expõe o objeto global **`build`** = `mainObject.main.modes["BUILD"]`, que é
  onde mora tudo que interessa depois de carregar um build.
- Já entrega funções helper prontas:
  - `newBuild()` — cria um build zerado.
  - `loadBuildFromXML(xmlText, name)` — carrega um build a partir do **XML cru**.
  - `loadBuildFromJSON(getItemsJSON, getPassiveSkillsJSON)` — importa de dados
    tipo API do jogo.

O arquivo **`.busted`** confirma que é assim que o projeto roda os testes:

```lua
default = {
    directory = "src",
    lpath = "../runtime/lua/?.lua;../runtime/lua/?/init.lua",
    helper = "HeadlessWrapper.lua",   -- <= carrega o engine sem GUI
    ROOT = { "../spec" },
    ["exclude-tags"] = "builds",
}
```

E o **`Dockerfile`** já monta o ambiente headless completo: Lua 5.1.5 + LuaJIT +
`busted`. Ou seja, o caminho "rodar o engine em container" **é literalmente o
que o projeto já faz** — a gente só reaproveita.

### 3. Os números saem por acesso a tabela Lua
Depois de carregar um build, as estatísticas ficam legíveis em campos simples.
Confirmado nos specs de sistema (`spec/System/*_spec.lua`):

- `build.calcsTab.mainOutput.TotalDPS` — DPS total do skill principal
- `build.calcsTab.mainOutput.Life`, `.Mana`, `.EnergyShield`
- `build.calcsTab.mainOutput.CritChance`, `.CritMultiplier`
- `build.calcsTab.mainOutput.MainHand.AverageHit`, `.Speed` ...
- `build.calcsTab.calcsOutput.PhysicalMaximumHitTaken`,
  `.FireMaximumHitTaken` ... (aba de cálculo defensivo detalhado)

Exemplo real tirado de `TestDefence_spec.lua`:

```lua
newBuild()
build.configTab.input.customMods = "100% increased maximum Mana"
build.configTab:BuildModList()
runCallback("OnFrame")
local mana = build.calcsTab.mainOutput.Mana   -- número pronto
```

---

## O pulo do gato (a parte chata, dita com honestidade)

### Deflate/Inflate estão STUBADOS no headless
No `HeadlessWrapper.lua`:

```lua
function Deflate(data)
    -- TODO: Might need this
    return ""
end
function Inflate(data)
    -- TODO: And this
    return ""
end
```

Consequência prática: o **"import/share code"** do PoB (aquele blocão base64
que a galera troca) é `base64( zlib-deflate( XML ) )`. Normalmente é o runtime
nativo que descomprime. No headless isso volta vazio.

**Solução (limpa):** o servidor MCP (Python) faz o base64-decode + `zlib`
por conta própria e entrega o **XML cru** para `loadBuildFromXML`. Python já tem
`base64` e `zlib` na stdlib — zero dependência extra. Não precisa nem tocar no
Lua para isso.

> ⚠️ **A verificar antes de codar:** confirmar exatamente a variação do encode
> (base64 padrão vs. base64url, e se há algum header/prefixo). Isso se resolve
> exportando um build pequeno no PoB e inspecionando os bytes, ou lendo a função
> de export no fonte (`ImportTab`/`Common`). Ver seção "Pendências de verificação".

### require de rede é bloqueado
O wrapper faz um hack em `require` para pular `lcurl.safe` (curl). Ou seja,
**sem rede** no headless. Funções que dependem de internet — importar personagem
do site pathofexile.com, consultar o trade — **não** funcionam headless sem
stub adicional. **Cálculo de build offline funciona 100%.** Para a v1 do MCP,
foque no offline; importação da conta/trade é escopo posterior.

---

## Arquitetura proposta

### Camadas

1. **Fork do PoB** (`PathOfBuilding-PoE2`)
   - Adicionar UM entrypoint headless novo, ex. `src/mcp_entry.lua`, que:
     - reaproveita `HeadlessWrapper.lua`,
     - lê um comando + payload (via stdin como JSON, ou argumentos),
     - chama `loadBuildFromXML(...)` / manipula `build`,
     - serializa as saídas de `build.calcsTab.mainOutput` para **JSON** no stdout.
   - Mudança cirúrgica. Não altera lógica de cálculo.

2. **Servidor MCP** (Python recomendado — `mcp` SDK oficial)
   - Faz base64+zlib do share code → XML.
   - Invoca o processo Lua/LuaJIT do fork (subprocess) OU mantém um processo
     persistente e conversa por stdin/stdout (mais rápido: evita reinicializar
     o engine a cada chamada, que é a parte lenta).
   - Expõe as ferramentas MCP e traduz JSON ⇄ resultado.

3. **Runtime**
   - Rodar dentro do container do `Dockerfile` do projeto (Lua 5.1.5 + LuaJIT
     já configurados) para não brigar com dependências no Windows.

### Decisão de performance importante
Inicializar o engine (carregar árvore passiva, gems, mods) é a parte cara.
**Não** dê spawn de um processo Lua por chamada. Suba **um** processo headless
persistente e mande comandos por stdin (um JSON por linha), lendo respostas por
stdout. Assim `newBuild()`/`loadBuildFromXML()` reaproveitam o engine já quente.

### Superfície de ferramentas MCP (v1)

| Ferramenta            | Entrada                        | Saída                                            |
|-----------------------|--------------------------------|--------------------------------------------------|
| `import_build`        | `code` (share code) ou `xml`   | resumo: classe, ascendência, nível, main skill   |
| `calc_stats`          | `code`/`xml`                   | DPS, Life, Mana, ES, resistências, crit, EHP     |
| `compare_builds`      | `codeA`, `codeB`               | diff lado a lado dos principais números          |
| `get_defenses`        | `code`/`xml`                   | max hit por tipo, mitigação, pools               |
| `set_config`          | `code`/`xml`, lista de mods    | recalcula e devolve stats (via `customMods`)     |

Escopo posterior (precisa de mais stub/rede): `edit_gear`, `swap_skill`,
`import_from_account`, `search_trade`.

---

## Roadmap sugerido

1. **Spike (meio dia):** subir o container do Dockerfile, rodar `busted` para
   provar que o headless funciona na sua máquina. Depois, um script Lua mínimo
   que faz `newBuild()` → seta um mod → imprime `mainOutput.TotalDPS`.
2. **Decodificar share code:** script Python que pega um code exportado do PoB,
   faz base64+zlib e cospe o XML. Validar contra o XML que o próprio PoB gera.
3. **Entrypoint headless:** `mcp_entry.lua` com loop stdin(JSON)→stdout(JSON).
4. **Servidor MCP:** 3–5 tools da tabela acima, chamando o entrypoint.
5. **Empacotar:** container + config MCP para plugar no Claude.

## Pendências de verificação (antes de codar pra valer)

- [ ] Confirmar formato exato do share code (base64 vs base64url, header/prefixo)
      lendo a função de export no fonte ou inspecionando um code real.
- [ ] Confirmar quais campos de `mainOutput` cobrem EHP/"effective HP" no PoE2
      (nomenclatura muda entre PoB1 e PoB2).
- [ ] Medir tempo de init do engine para decidir tamanho do pool de processos.
- [ ] Checar se `loadBuildFromXML` exige a árvore passiva na versão certa do
      jogo embutida no fork (versionamento de dados).

## Referências no repositório

- `src/HeadlessWrapper.lua` — wrapper headless (stubs + helpers `newBuild`,
  `loadBuildFromXML`, `loadBuildFromJSON`).
- `.busted` — config que prova o setup headless via `HeadlessWrapper.lua`.
- `Dockerfile` / `docker-compose.yml` — ambiente Lua 5.1.5 + LuaJIT + busted.
- `spec/System/*_spec.lua` — exemplos de como ler `build.calcsTab.mainOutput.*`
  e `build.calcsTab.calcsOutput.*`.
- `LICENSE.md` — MIT.
