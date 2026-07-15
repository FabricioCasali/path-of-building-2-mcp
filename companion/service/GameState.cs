using PoeCompanion.Service.Logging;
using PoeCompanion.Service.Lore;

namespace PoeCompanion.Service;

/// <summary>Point-in-time view of the player's progress for the UI.</summary>
public sealed record StateSnapshot(string Frontier, IReadOnlyList<string> Characters);

/// <summary>
/// Thread-safe home for the <see cref="LoreGate"/> and <see cref="Chronicle"/>. The
/// log watcher feeds events from a background task while WebSocket requests read the
/// frontier / timeline / build prompts concurrently, so every access is serialized
/// under one lock. (LoreGate and Chronicle are intentionally not thread-safe on their
/// own, matching the Python port style.)
/// </summary>
public sealed class GameState(LoreGate gate, Chronicle chronicle)
{
    private readonly object _lock = new();

    public void Feed(LogEvent ev)
    {
        lock (_lock)
        {
            gate.Feed(ev);
            chronicle.Feed(ev);
        }
    }

    public LorePrompt BuildPrompt(string question)
    {
        lock (_lock) return gate.BuildPrompt(question);
    }

    public StateSnapshot Snapshot()
    {
        lock (_lock) return new StateSnapshot(gate.Frontier.Label(), gate.CharactersMet());
    }

    /// <summary>Chronological journey entries for the Lore timeline / Story.</summary>
    public IReadOnlyList<ChronicleEntry> Timeline(int limit = 40)
    {
        lock (_lock) return chronicle.Timeline(limit);
    }

    /// <summary>Live situational context for the "Agora" strip.</summary>
    public ContextSnapshot Context()
    {
        lock (_lock)
            return new ContextSnapshot(
                chronicle.CurrentLocation, chronicle.RecentDeaths(), chronicle.RecentCharacters());
    }
}
