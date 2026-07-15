using PoeCompanion.Service.Logging;
using PoeCompanion.Service.Lore;

namespace PoeCompanion.Tests;

/// <summary>
/// Port-fidelity tests: the C# LogWatcher/LoreGate must produce the exact same
/// frontier, characters-met, and journal as the Python reference (pob_mcp.lore)
/// over the same synthetic Client.txt. The golden values below were captured from
/// the Python oracle — see companion/tests/fixtures/sample-client.txt.
/// </summary>
public class LorePortTests
{
    private static string FixturePath =>
        Path.Combine(AppContext.BaseDirectory, "fixtures", "sample-client.txt");

    private static LoreGate ReplayFixture()
    {
        var gate = new LoreGate();
        foreach (var ev in LogWatcher.Replay(FixturePath))
            gate.Feed(ev);
        return gate;
    }

    [Fact]
    public void Frontier_MatchesPythonOracle()
    {
        var gate = ReplayFixture();
        Assert.Equal("Endgame / mapas (área nível 79)", gate.Frontier.Label());
    }

    [Fact]
    public void CharactersMet_MatchesPythonOracle()
    {
        var gate = ReplayFixture();
        Assert.Equal(new[] { "The Hooded One", "Doryani", "Rog" }, gate.CharactersMet());
    }

    [Fact]
    public void Journal_MatchesPythonOracle()
    {
        var gate = ReplayFixture();
        var journal = gate.Journal()
            .Select(b => (b.Speaker, b.Text, b.FrontierLabel))
            .ToArray();

        Assert.Equal(new[]
        {
            ("The Hooded One", "You have made it. Good.", "Ato 1 Normal (área nível 2)"),
            ("Doryani", "We meet again, exile.", "Ato 2 Normal (área nível 12)"),
            // Rog was witnessed after backtracking to G4_town, which must NOT lower the
            // endgame frontier — so his beat is tagged at the map's level 65, not Act 1.
            ("Rog", "We do close eventually, you know.", "Endgame / mapas (área nível 65)"),
        }, journal);
    }

    [Fact]
    public void BuildPrompt_UsesDefaultGatedSystemPrompt()
    {
        var gate = ReplayFixture();
        var p = gate.BuildPrompt("quem é a Rainha?");
        Assert.Equal(LoreGate.DefaultSystem, p.System);
        Assert.Equal("quem é a Rainha?", p.Question);
        Assert.Equal("Endgame / mapas (área nível 79)", p.Frontier);
        Assert.Contains("FRONTEIRA DE PROGRESSO DO JOGADOR", p.Context);
        Assert.Contains("The Hooded One", p.Context); // characters-met line
    }
}

/// <summary>Unit checks on the parser/classifier, independent of the fixture.</summary>
public class LogParserTests
{
    [Theory]
    [InlineData("G1_1", "campaign", 1, "normal", false)]
    [InlineData("G3_2", "campaign", 3, "normal", false)]
    [InlineData("G4_1", "campaign", 1, "cruel", false)]
    [InlineData("G4_town", "town", 1, "cruel", true)]
    [InlineData("MapBeach", "map", null, "endgame", false)]
    [InlineData("G_Endgame_Town", "town", null, "endgame", true)]
    [InlineData("Sanctum_1_Foyer_1", "league", null, null, false)]
    public void ClassifyArea_InfersActAndDifficulty(
        string code, string kind, int? act, string? difficulty, bool isTown)
    {
        var info = LogParser.ClassifyArea(code);
        Assert.Equal(kind, info["kind"]);
        Assert.Equal(act, info["act"]);
        Assert.Equal(difficulty, info["difficulty"]);
        Assert.Equal(isTown, info["is_town"]);
    }

    [Fact]
    public void ParseLine_AcceptsNpcDialogue_WhenSubIsKnownMessageSub()
    {
        var subs = new HashSet<string> { "a1a1a1a1" };
        var ev = LogParser.ParseLine(
            "2025/07/14 20:01:00 30 a1a1a1a1 [INFO Client 100] The Hooded One: You have made it.", subs);
        Assert.NotNull(ev);
        Assert.Equal("dialogue", ev!.Kind);
        Assert.Equal("The Hooded One", ev.GetString("speaker"));
        Assert.True(ev.Get<bool>("likely_npc"));
    }

    [Fact]
    public void ParseLine_RejectsEngineNoise_WhenSubIsUnknown()
    {
        // "Tile hash: 1909094995" fits "X: y" but comes from a non-message subsystem.
        var subs = new HashSet<string> { "a1a1a1a1" };
        var ev = LogParser.ParseLine(
            "2025/07/14 20:02:00 40 b2b2b2b2 [DEBUG Client 100] Tile hash: 1909094995", subs);
        Assert.Null(ev);
    }

    [Fact]
    public void ParseLine_FlagsAllCapsSpeakerAsNotNpc()
    {
        var subs = new HashSet<string> { "a1a1a1a1" };
        var ev = LogParser.ParseLine(
            "2025/07/14 20:16:00 110 a1a1a1a1 [INFO Client 100] CAPTAIN: HALT", subs);
        Assert.NotNull(ev);
        Assert.Equal("dialogue", ev!.Kind);
        Assert.False(ev.Get<bool>("likely_npc"));
    }
}

/// <summary>Monotonic frontier behaviour — the spoiler ceiling only ever rises.</summary>
public class FrontierTests
{
    [Fact]
    public void IsAheadOf_RanksDifficultyThenActThenAreaLevel()
    {
        Assert.True(new Frontier("cruel", 1, 5).IsAheadOf(new Frontier("normal", 3, 40)));
        Assert.True(new Frontier("normal", 2, 10).IsAheadOf(new Frontier("normal", 1, 99)));
        Assert.True(new Frontier("normal", 1, 20).IsAheadOf(new Frontier("normal", 1, 19)));
        Assert.False(new Frontier("normal", 1, 20).IsAheadOf(new Frontier("normal", 1, 20)));
        Assert.True(new Frontier("endgame", 1, 65).IsAheadOf(new Frontier("cruel", 3, 80)));
    }
}
