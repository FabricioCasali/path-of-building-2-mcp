using PoeCompanion.Service.Logging;
using PoeCompanion.Service.Lore;

namespace PoeCompanion.Service;

/// <summary>Point-in-time view of the player's progress for the UI.</summary>
public sealed record StateSnapshot(string Frontier, IReadOnlyList<string> Characters);

/// <summary>
/// Thread-safe home for the <see cref="LoreGate"/>. The log watcher feeds events
/// from a background task while WebSocket requests read the frontier / build prompts
/// concurrently, so every access is serialized under one lock. (LoreGate itself is
/// a faithful port and intentionally not thread-safe, matching the Python module.)
/// </summary>
public sealed class GameState(LoreGate gate)
{
    private readonly object _lock = new();

    public void Feed(LogEvent ev)
    {
        lock (_lock) gate.Feed(ev);
    }

    public LorePrompt BuildPrompt(string question)
    {
        lock (_lock) return gate.BuildPrompt(question);
    }

    public StateSnapshot Snapshot()
    {
        lock (_lock) return new StateSnapshot(gate.Frontier.Label(), gate.CharactersMet());
    }
}
