using PoeCompanion.Service.Lore;

namespace PoeCompanion.Service.Llm;

/// <summary>
/// The seam that decouples the app from any single AI backend. During development
/// this is <see cref="ClaudeCliProvider"/> (drives the local `claude` CLI on the
/// Max subscription — no metered API). For the final product, swap in an
/// AnthropicApiProvider (metered Claude API) or any other agent, without touching
/// <see cref="LoreGate"/> or the UI.
/// </summary>
public interface ILlmProvider
{
    /// <summary>
    /// Stream the model's answer to a gated lore prompt as text fragments, in order.
    /// Throws <see cref="LlmException"/> on a provider/auth error.
    /// </summary>
    IAsyncEnumerable<string> StreamAsync(LorePrompt prompt, string model, CancellationToken ct = default);
}

/// <summary>An LLM provider failure (spawn failed, non-zero exit, auth error, ...).</summary>
public sealed class LlmException(string message) : Exception(message);
