using System.Globalization;

namespace PoeCompanion.Service.Lore;

/// <summary>
/// How far the player has progressed — the spoiler ceiling. Faithful port of the
/// frozen <c>Frontier</c> dataclass in <c>pob_mcp/lore.py</c>.
/// </summary>
public sealed record Frontier(
    string Difficulty = "normal",
    int Act = 1,
    int AreaLevel = 1,
    string? AreaCode = null,
    string? Kind = null)
{
    // Monotonic ordering of difficulties. Endgame maps sit above the campaign.
    private static readonly Dictionary<string, int> DifficultyRank = new()
    {
        ["normal"] = 0,
        ["cruel"] = 1,
        ["unknown"] = 1,
        ["endgame"] = 2,
    };

    private (int, int, int) Key() =>
        (DifficultyRank.GetValueOrDefault(Difficulty, 0), Act, AreaLevel);

    /// <summary>True if this frontier is strictly further than <paramref name="other"/>.</summary>
    public bool IsAheadOf(Frontier other)
    {
        var a = Key();
        var b = other.Key();
        // Lexicographic tuple comparison, matching Python's a > b on tuples.
        if (a.Item1 != b.Item1) return a.Item1 > b.Item1;
        if (a.Item2 != b.Item2) return a.Item2 > b.Item2;
        return a.Item3 > b.Item3;
    }

    /// <summary>Human-readable frontier label (PT-BR, matching lore.py verbatim).</summary>
    public string Label()
    {
        if (Kind == "map" || Difficulty == "endgame")
            return string.Create(CultureInfo.InvariantCulture, $"Endgame / mapas (área nível {AreaLevel})");

        var diff = Difficulty switch
        {
            "normal" => "Normal",
            "cruel" => "Cruel",
            _ => Difficulty,
        };
        return string.Create(CultureInfo.InvariantCulture, $"Ato {Act} {diff} (área nível {AreaLevel})");
    }
}
