using PoeCompanion.Service;
using PoeCompanion.Service.Logging;

namespace PoeCompanion.Tests;

/// <summary>
/// Verifies the Chronicle builds the expected ordered journey from the same
/// synthetic Client.txt used by the port tests. Unlike LoreGate's monotonic
/// frontier, the Chronicle records the literal location at each moment (so Rog,
/// witnessed after porting back to a Cruel town, is tagged there — not endgame).
/// </summary>
public class ChronicleTests
{
    private static string FixturePath =>
        Path.Combine(AppContext.BaseDirectory, "fixtures", "sample-client.txt");

    private static Chronicle Build()
    {
        var chronicle = new Chronicle();
        foreach (var ev in LogWatcher.Replay(FixturePath))
            chronicle.Feed(ev);
        return chronicle;
    }

    [Fact]
    public void Timeline_RecordsActsDeathsBeatsAndEndgameInOrder()
    {
        var timeline = Build().Timeline()
            .Select(e => (e.Kind, e.Text))
            .ToArray();

        Assert.Equal(new[]
        {
            ("act", "Ato 1 Normal"),
            ("beat", "The Hooded One: You have made it. Good."),
            ("act", "Ato 2 Normal"),
            ("beat", "Doryani: We meet again, exile."),
            ("act", "Ato 3 Normal"),
            ("death", "Morreu"),
            ("act", "Ato 1 Cruel"),
            ("endgame", "Chegou ao endgame (mapas)"),
            ("beat", "Rog: We do close eventually, you know."),
        }, timeline);
    }

    [Fact]
    public void BacktrackToTown_DoesNotDuplicateActEntry()
    {
        // Entering G4_town after G4_1 is the same (act, difficulty) — no new act entry.
        var acts = Build().Timeline().Count(e => e.Kind == "act" && e.Text == "Ato 1 Cruel");
        Assert.Equal(1, acts);
    }

    [Fact]
    public void RogBeat_TaggedAtLiteralTownLocation_NotEndgameFrontier()
    {
        var rog = Build().Timeline().Single(e => e.Text.StartsWith("Rog:"));
        Assert.Equal("Ato 1 Cruel (área nível 22)", rog.Location);
    }

    [Fact]
    public void CurrentLocation_IsTheLatestArea()
    {
        Assert.Equal("Endgame / mapas (área nível 79)", Build().CurrentLocation);
    }

    [Fact]
    public void RecentCharacters_AreNewestFirst()
    {
        Assert.Equal(new[] { "Rog", "Doryani", "The Hooded One" }, Build().RecentCharacters());
    }

    [Fact]
    public void RecentDeaths_OutsideWindow_AreNotCounted()
    {
        // The lone death is ~18 log-minutes before the final event — outside the
        // 10-minute recency window, so it does not show as "recent".
        Assert.Equal(0, Build().RecentDeaths());
    }
}
