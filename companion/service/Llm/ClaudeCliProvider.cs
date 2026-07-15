using System.Diagnostics;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using PoeCompanion.Service.Lore;

namespace PoeCompanion.Service.Llm;

/// <summary>
/// Drives the local <c>claude</c> CLI in headless/print mode as the LLM backend.
///
/// Why the CLI and not the Agent SDK: the SDK requires a metered ANTHROPIC_API_KEY,
/// whereas <c>claude -p</c> uses the logged-in Claude Code subscription session —
/// which is what we have during development. For production this whole class is
/// replaced by an API-key provider behind <see cref="ILlmProvider"/>.
///
/// Invocation (verified against claude 2.1.x):
///   claude -p "&lt;question&gt;" --output-format stream-json --include-partial-messages
///          --verbose --disable-slash-commands --model &lt;model&gt;
///          --system-prompt-file &lt;tmp: system + context&gt;
///
/// stream-json emits newline-delimited JSON. We stream token deltas from
/// <c>stream_event / content_block_delta / text_delta</c> when partial messages are
/// on, and fall back to the whole <c>assistant</c> message text otherwise.
/// </summary>
public sealed class ClaudeCliProvider(string executable = "claude") : ILlmProvider
{
    private enum Source { None, Delta, Assistant }

    public async IAsyncEnumerable<string> StreamAsync(
        LorePrompt prompt, string model, [EnumeratorCancellation] CancellationToken ct = default)
    {
        // System + gated context go into a temp file (avoids arg-length/escaping
        // issues); the short question is the -p argument.
        var sysFile = Path.Combine(Path.GetTempPath(), $"poe-lore-{Guid.NewGuid():N}.txt");
        await File.WriteAllTextAsync(sysFile, prompt.System + "\n\n" + prompt.Context, ct)
            .ConfigureAwait(false);

        // Neutral working dir so the CLI doesn't pull in this repo's CLAUDE.md / skills.
        var workDir = Path.GetTempPath();

        var psi = new ProcessStartInfo(executable)
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = workDir,
            StandardOutputEncoding = Encoding.UTF8,
        };
        psi.ArgumentList.Add("-p");
        psi.ArgumentList.Add(prompt.Question);
        psi.ArgumentList.Add("--output-format");
        psi.ArgumentList.Add("stream-json");
        psi.ArgumentList.Add("--include-partial-messages");
        psi.ArgumentList.Add("--verbose");
        psi.ArgumentList.Add("--disable-slash-commands");
        psi.ArgumentList.Add("--model");
        psi.ArgumentList.Add(model);
        psi.ArgumentList.Add("--system-prompt-file");
        psi.ArgumentList.Add(sysFile);
        // Force subscription auth during dev: an inherited key would meter usage.
        psi.Environment.Remove("ANTHROPIC_API_KEY");

        try
        {
            using var process = new Process { StartInfo = psi };
            try
            {
                process.Start();
            }
            catch (Exception ex)
            {
                throw new LlmException($"não consegui iniciar '{executable}': {ex.Message}. " +
                                       "Confirme que o Claude Code está no PATH e logado (claude auth status).");
            }

            process.StandardInput.Close();

            var stderr = new StringBuilder();
            var stderrTask = DrainStderrAsync(process, stderr, ct);

            var sawPartial = false;
            string? errorMsg = null;

            while (true)
            {
                string? line;
                try
                {
                    line = await process.StandardOutput.ReadLineAsync(ct).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    TryKill(process);
                    throw;
                }
                if (line is null) break;
                if (line.Length == 0) continue;

                var (texts, source, isError, err) = ParseChunk(line);
                if (isError) errorMsg ??= err;
                if (texts.Count == 0) continue;

                if (source == Source.Delta)
                {
                    sawPartial = true;
                    foreach (var t in texts) yield return t;
                }
                else if (source == Source.Assistant && !sawPartial)
                {
                    // Fallback when partial messages aren't emitted: the whole message.
                    foreach (var t in texts) yield return t;
                }
            }

            await process.WaitForExitAsync(ct).ConfigureAwait(false);
            await stderrTask.ConfigureAwait(false);

            if (errorMsg is not null)
                throw new LlmException($"claude retornou erro: {errorMsg}");
            if (process.ExitCode != 0)
            {
                var tail = stderr.ToString();
                if (tail.Length > 400) tail = tail[^400..];
                throw new LlmException($"claude saiu com código {process.ExitCode}. {tail}".Trim());
            }
        }
        finally
        {
            try { File.Delete(sysFile); } catch { /* best-effort cleanup */ }
        }
    }

    private static async Task DrainStderrAsync(Process process, StringBuilder sink, CancellationToken ct)
    {
        try
        {
            string? line;
            while ((line = await process.StandardError.ReadLineAsync(ct).ConfigureAwait(false)) is not null)
                sink.AppendLine(line);
        }
        catch (OperationCanceledException) { /* shutting down */ }
    }

    private static void TryKill(Process process)
    {
        try { if (!process.HasExited) process.Kill(entireProcessTree: true); }
        catch { /* already gone */ }
    }

    // Parse one NDJSON line into any text fragments plus its source/error status.
    private static (List<string> texts, Source source, bool isError, string? error) ParseChunk(string line)
    {
        var texts = new List<string>();
        JsonDocument doc;
        try { doc = JsonDocument.Parse(line); }
        catch (JsonException) { return (texts, Source.None, false, null); }

        using (doc)
        {
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("type", out var typeEl))
                return (texts, Source.None, false, null);

            switch (typeEl.GetString())
            {
                case "stream_event":
                    // {"event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"..."}}}
                    if (root.TryGetProperty("event", out var evEl) &&
                        evEl.TryGetProperty("type", out var evType) &&
                        evType.GetString() == "content_block_delta" &&
                        evEl.TryGetProperty("delta", out var delta) &&
                        delta.TryGetProperty("type", out var dType) &&
                        dType.GetString() == "text_delta" &&
                        delta.TryGetProperty("text", out var dText))
                    {
                        var s = dText.GetString();
                        if (!string.IsNullOrEmpty(s)) texts.Add(s);
                        return (texts, Source.Delta, false, null);
                    }
                    return (texts, Source.None, false, null);

                case "assistant":
                    // {"message":{"content":[{"type":"text","text":"..."}]}}
                    if (root.TryGetProperty("message", out var msg) &&
                        msg.TryGetProperty("content", out var content) &&
                        content.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var block in content.EnumerateArray())
                        {
                            if (block.TryGetProperty("type", out var bType) && bType.GetString() == "text" &&
                                block.TryGetProperty("text", out var bText))
                            {
                                var s = bText.GetString();
                                if (!string.IsNullOrEmpty(s)) texts.Add(s);
                            }
                        }
                    }
                    return (texts, Source.Assistant, false, null);

                case "result":
                    var isError = root.TryGetProperty("is_error", out var ie) &&
                                  ie.ValueKind == JsonValueKind.True;
                    string? err = null;
                    if (isError)
                    {
                        err = root.TryGetProperty("result", out var r) ? r.GetString() : null;
                        err ??= root.TryGetProperty("subtype", out var st) ? st.GetString() : "erro desconhecido";
                    }
                    return (texts, Source.None, isError, err);

                default:
                    return (texts, Source.None, false, null);
            }
        }
    }
}
