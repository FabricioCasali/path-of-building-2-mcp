using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using PoeCompanion.Service.Llm;

namespace PoeCompanion.Service.Api;

/// <summary>
/// The localhost WebSocket protocol between the Electron overlay and this service.
///
/// Client → server (one JSON object per message):
///   {"type":"state"}                              → ask for current progress
///   {"type":"ask","question":"...","model":"..."} → ask a lore question (model optional)
///
/// Server → client:
///   {"type":"state","frontier":"...","characters":[...]}
///   {"type":"start","frontier":"..."}   then N× {"type":"chunk","text":"..."}   then {"type":"done"}
///   {"type":"error","message":"..."}
/// </summary>
public static class CompanionSocket
{
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    public static async Task HandleAsync(WebSocket socket, GameState state, ILlmProvider llm,
        CompanionOptions options, ILogger logger, CancellationToken ct)
    {
        while (socket.State == WebSocketState.Open && !ct.IsCancellationRequested)
        {
            var raw = await ReceiveTextAsync(socket, ct).ConfigureAwait(false);
            if (raw is null) break; // close received

            string type;
            JsonDocument doc;
            try
            {
                doc = JsonDocument.Parse(raw);
                type = doc.RootElement.TryGetProperty("type", out var t) ? t.GetString() ?? "" : "";
            }
            catch (JsonException)
            {
                await SendAsync(socket, new { type = "error", message = "JSON inválido." }, ct).ConfigureAwait(false);
                continue;
            }

            using (doc)
            {
                switch (type)
                {
                    case "state":
                    {
                        var snap = state.Snapshot();
                        await SendAsync(socket, new { type = "state", frontier = snap.Frontier, characters = snap.Characters }, ct)
                            .ConfigureAwait(false);
                        break;
                    }
                    case "ask":
                    {
                        var question = doc.RootElement.TryGetProperty("question", out var q) ? q.GetString() : null;
                        if (string.IsNullOrWhiteSpace(question))
                        {
                            await SendAsync(socket, new { type = "error", message = "Pergunta vazia." }, ct).ConfigureAwait(false);
                            break;
                        }
                        var model = doc.RootElement.TryGetProperty("model", out var m) && !string.IsNullOrWhiteSpace(m.GetString())
                            ? m.GetString()!
                            : options.Model;
                        await StreamAnswerAsync(socket, state, llm, question!, model, logger, ct).ConfigureAwait(false);
                        break;
                    }
                    default:
                        await SendAsync(socket, new { type = "error", message = $"tipo desconhecido: '{type}'." }, ct)
                            .ConfigureAwait(false);
                        break;
                }
            }
        }

        if (socket.State is WebSocketState.Open or WebSocketState.CloseReceived)
        {
            try { await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "bye", CancellationToken.None).ConfigureAwait(false); }
            catch { /* client already gone */ }
        }
    }

    private static async Task StreamAnswerAsync(WebSocket socket, GameState state, ILlmProvider llm,
        string question, string model, ILogger logger, CancellationToken ct)
    {
        var prompt = state.BuildPrompt(question);
        await SendAsync(socket, new { type = "start", frontier = prompt.Frontier }, ct).ConfigureAwait(false);
        try
        {
            await foreach (var fragment in llm.StreamAsync(prompt, model, ct).ConfigureAwait(false))
                await SendAsync(socket, new { type = "chunk", text = fragment }, ct).ConfigureAwait(false);
            await SendAsync(socket, new { type = "done" }, ct).ConfigureAwait(false);
        }
        catch (OperationCanceledException) { throw; }
        catch (LlmException ex)
        {
            logger.LogWarning(ex, "Falha do provedor LLM.");
            await SendAsync(socket, new { type = "error", message = ex.Message }, ct).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Erro inesperado ao responder.");
            await SendAsync(socket, new { type = "error", message = "Erro inesperado ao gerar a resposta." }, ct)
                .ConfigureAwait(false);
        }
    }

    private static async Task SendAsync(WebSocket socket, object payload, CancellationToken ct)
    {
        if (socket.State != WebSocketState.Open) return;
        var bytes = JsonSerializer.SerializeToUtf8Bytes(payload, Json);
        await socket.SendAsync(bytes, WebSocketMessageType.Text, endOfMessage: true, ct).ConfigureAwait(false);
    }

    private static async Task<string?> ReceiveTextAsync(WebSocket socket, CancellationToken ct)
    {
        var buffer = new byte[4096];
        using var ms = new MemoryStream();
        while (true)
        {
            WebSocketReceiveResult result;
            try
            {
                result = await socket.ReceiveAsync(buffer, ct).ConfigureAwait(false);
            }
            catch (WebSocketException)
            {
                return null;
            }
            if (result.MessageType == WebSocketMessageType.Close) return null;
            ms.Write(buffer, 0, result.Count);
            if (result.EndOfMessage) break;
        }
        return Encoding.UTF8.GetString(ms.ToArray());
    }
}
