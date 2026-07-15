using PoeCompanion.Service.Logging;
using PoeCompanion.Service.Lore;

namespace PoeCompanion.Service;

/// <summary>One chronological entry in the player's journey.</summary>
/// <param name="Kind">act | endgame | level | death | beat</param>
/// <param name="Ts">When it happened (from the log line).</param>
/// <param name="Text">Human-readable summary.</param>
/// <param name="Location">Where the player was at that moment (frontier-style label).</param>
public sealed record ChronicleEntry(string Kind, DateTime Ts, string Text, string Location);

/// <summary>Compact live context for the "Agora" strip.</summary>
public sealed record ContextSnapshot(
    string Location, int RecentDeaths, IReadOnlyList<string> RecentCharacters);

/// <summary>
/// Accumulates a chronological timeline of the run from log events: act
/// transitions, endgame arrival, level milestones, deaths, and witnessed
/// boss/NPC beats. Feeds the Lore "timeline" now and the Story chronicle later.
///
/// Unlike <see cref="LoreGate"/> (a monotonic spoiler ceiling), this keeps the
/// literal, ordered history and the *current* location (which can move backwards,
/// e.g. porting to town). Not thread-safe on its own — guarded by <see cref="GameState"/>.
/// </summary>
public sealed class Chronicle
{
    private const int DeathWindowMinutes = 10;

    private readonly List<ChronicleEntry> _entries = new();
    private readonly HashSet<(string, string)> _seenBeats = new();

    private string _location = new Frontier().Label();
    private (int act, string diff)? _lastActKey;
    private bool _seenEndgame;
    private DateTime _latestTs = DateTime.MinValue;

    public string CurrentLocation => _location;

    public void Feed(LogEvent ev)
    {
        if (ev.Ts > _latestTs) _latestTs = ev.Ts;

        switch (ev.Kind)
        {
            case "area":
                FeedArea(ev);
                break;
            case "level_up":
                if (ev.Get<int>("level") is var lvl && lvl > 0 && lvl % 10 == 0)
                    Add("level", ev.Ts, $"Nível {lvl}");
                break;
            case "death":
                Add("death", ev.Ts, "Morreu");
                break;
            case "dialogue" when ev.Get<bool>("likely_npc"):
                var speaker = ev.GetString("speaker") ?? "";
                var text = ev.GetString("text") ?? "";
                if (_seenBeats.Add((speaker, text)))
                    Add("beat", ev.Ts, $"{speaker}: {text}");
                break;
        }
    }

    private void FeedArea(LogEvent ev)
    {
        var d = ev.Data;
        var kind = d.GetValueOrDefault("kind") as string;
        var diff = d.GetValueOrDefault("difficulty") as string ?? "normal";
        var act = d.GetValueOrDefault("act") as int?;
        var areaLevel = ev.Get<int>("area_level");
        var code = d.GetValueOrDefault("code") as string;

        // Literal current location (non-monotonic) for the "Agora" readout.
        _location = new Frontier(diff, act ?? 1, areaLevel, code, kind).Label();

        // Endgame arrival, once.
        if (kind == "map" && !_seenEndgame)
        {
            _seenEndgame = true;
            Add("endgame", ev.Ts, "Chegou ao endgame (mapas)");
            return;
        }

        // Act transition (campaign areas only).
        if (act is int a && (kind == "campaign" || kind == "town"))
        {
            var key = (a, diff);
            if (_lastActKey != key)
            {
                _lastActKey = key;
                Add("act", ev.Ts, $"Ato {a} {DiffLabel(diff)}");
            }
        }
    }

    private void Add(string kind, DateTime ts, string text) =>
        _entries.Add(new ChronicleEntry(kind, ts, text, _location));

    private static string DiffLabel(string diff) => diff switch
    {
        "normal" => "Normal",
        "cruel" => "Cruel",
        _ => diff,
    };

    /// <summary>The most recent <paramref name="limit"/> entries, oldest first.</summary>
    public IReadOnlyList<ChronicleEntry> Timeline(int limit = 40) =>
        _entries.Count <= limit ? _entries.ToList() : _entries.GetRange(_entries.Count - limit, limit);

    /// <summary>Deaths within the last few log-minutes of the latest event.</summary>
    public int RecentDeaths()
    {
        var cutoff = _latestTs.AddMinutes(-DeathWindowMinutes);
        return _entries.Count(e => e.Kind == "death" && e.Ts >= cutoff);
    }

    /// <summary>Distinct NPC/boss speakers most recently witnessed, newest first.</summary>
    public IReadOnlyList<string> RecentCharacters(int n = 5)
    {
        var seen = new HashSet<string>();
        var result = new List<string>();
        for (var i = _entries.Count - 1; i >= 0 && result.Count < n; i--)
        {
            if (_entries[i].Kind != "beat") continue;
            var speaker = _entries[i].Text.Split(':', 2)[0];
            if (seen.Add(speaker)) result.Add(speaker);
        }
        return result;
    }
}
