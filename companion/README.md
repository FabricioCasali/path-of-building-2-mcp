# PoE2 Companion — MVP: Lore Q&A no hotkey

Um overlay para **Path of Exile 2** que responde perguntas de **história/lore sem spoiler**,
travadas no ponto exato onde o seu personagem chegou. Aperta um hotkey, pergunta
"quem é Doryani?", e a resposta chega streamando — usando só o que o jogo já te mostrou.

Este é o **1º milestone** de um assistente maior (coaching de build, trade, e uma
crônica narrativa do personagem virão depois).

## Arquitetura

```
Electron (overlay transparent + hotkey global)   companion/app
    │  WebSocket (localhost:8848)
    ▼
C#/.NET service                                   companion/service
    ├─ LogWatcher   → segue o Client.txt do PoE2 (porta fiel de pob_mcp/logwatch.py)
    ├─ LoreGate     → fronteira de progresso + beats + prompt gated (porta de pob_mcp/lore.py)
    └─ ILlmProvider
         └─ ClaudeCliProvider → roda `claude -p` na SUA ASSINATURA (sem custo de API)
```

O `LogWatcher` roda contínuo e mantém a fronteira de spoiler atualizada; o hotkey
só dispara o prompt gated + a chamada ao LLM.

> **Dev vs. produto final:** hoje o LLM é o `claude` CLI (assinatura Claude Code).
> A interface `ILlmProvider` é a costura: no produto final basta trocar por um
> `AnthropicApiProvider` (Claude API metered) ou outro agente — sem tocar no resto.

## Pré-requisitos

- **.NET 10 SDK** (`dotnet --version`)
- **Node 20+** e **npm** (para o Electron)
- **Claude Code** logado na assinatura (`claude auth status` — sem `ANTHROPIC_API_KEY`)
- **PoE2** rodando em **janela sem borda** (borderless/windowed). Overlays não
  aparecem sobre fullscreen exclusivo.

## Configuração

Edite `companion/app/config.json`:

```json
{
  "clientTxtPath": "H:/SteamLibrary/steamapps/common/Path of Exile 2/logs/Client.txt",
  "model": "sonnet",
  "hotkey": "CommandOrControl+Shift+Space",
  "serviceUrl": "http://127.0.0.1:8848",
  "spawnService": true
}
```

- `clientTxtPath` — caminho do seu `Client.txt` (ajuste a unidade/pasta do Steam).
- `model` — `sonnet` (Sonnet 5, padrão), `haiku` (mais barato), `opus` (mais pesado).
- `spawnService: true` — o app sobe o serviço C# sozinho. Deixe `false` se preferir
  rodar o serviço à parte.

## Rodando

1. **Build do serviço** (uma vez):
   ```bash
   cd companion/service
   dotnet build -c Debug
   ```
2. **Instalar o Electron** (uma vez):
   ```bash
   cd companion/app
   npm install
   ```
3. **Iniciar o overlay**:
   ```bash
   cd companion/app
   npm start
   ```
   Com `spawnService: true`, isso já sobe o serviço C#. A janela nasce escondida.
4. **No jogo**, aperte o **hotkey** (`Ctrl+Shift+Space`) para abrir/fechar. Digite a
   pergunta, `Enter` envia, `Esc` esconde.

### Rodar o serviço separado (opcional)

```bash
cd companion/service/bin/Debug/net10.0
Companion__ClientTxtPath="H:/.../Path of Exile 2/logs/Client.txt" dotnet PoeCompanion.Service.dll
# health:  curl http://127.0.0.1:8848/health
```

## Testes

Testes de **fidelidade do port** (o C# tem que bater com o oráculo Python
`pob_mcp.lore`/`logwatch` sobre a mesma amostra):

```bash
cd companion/tests/PoeCompanion.Tests
dotnet test
```

## Protocolo WebSocket (`/ws`)

Cliente → serviço:
- `{"type":"state"}` → pede a fronteira/personagens atuais
- `{"type":"ask","question":"...","model":"..."}` → pergunta (model opcional)

Serviço → cliente:
- `{"type":"state","frontier":"...","characters":[...]}`
- `{"type":"start","frontier":"..."}` · `{"type":"chunk","text":"..."}`× · `{"type":"done"}`
- `{"type":"error","message":"..."}`

## Notas

- **Anti-spoiler:** a fronteira é monotônica (voltar pra town não abaixa) e as
  respostas são ancoradas nos beats que o jogo comprovadamente te mostrou.
- **ToS:** só lemos o `Client.txt` (arquivo que o próprio cliente escreve) — nada de
  leitura de memória. Sancionado pela GGG.
- **Reuso:** os módulos Python em `../server/pob_mcp` seguem como referência e serão
  reaproveitados (engine PoB, trade) nas próximas fases via MCP.
