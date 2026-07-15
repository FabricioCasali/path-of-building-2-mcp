using System.Globalization;
using System.Text.RegularExpressions;

namespace PoeCompanion.Service.Logging;

/// <summary>
/// Turns raw Client.txt lines into <see cref="LogEvent"/>s. Faithful port of the
/// parsing half of <c>pob_mcp/logwatch.py</c> — same regexes, same area
/// classification, same NPC-vs-engine-noise heuristic. Keep behaviour in lockstep
/// with the Python module; the Python <c>_main()</c> is the test oracle.
/// </summary>
public static partial class LogParser
{
    // Every line: "YYYY/MM/DD HH:MM:SS <ms> <hex> [<LEVEL> Client <n>] <message>".
    // The <hex> is a subsystem id: the message log (chat, whispers, level ups,
    // deaths, NPC dialogue) shares one id; engine diagnostics use different ones.
    [GeneratedRegex(@"^(?<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \d+ (?<sub>[0-9a-f]+) \[(?<level>\w+) Client \d+\] (?<msg>.*)$")]
    private static partial Regex LineRe();

    // Event kinds that unambiguously come from the message-log subsystem; seeing
    // one teaches us that subsystem's hex id.
    public static readonly IReadOnlySet<string> MessageKinds =
        new HashSet<string> { "chat", "whisper", "level_up", "death" };

    // --- message patterns (matched against the message body only) ---
    [GeneratedRegex(@"^Generating level (?<lvl>\d+) area ""(?<code>[^""]+)""(?: with seed (?<seed>\d+))?")]
    private static partial Regex AreaRe();

    [GeneratedRegex(@"^: (?<char>.+?) \((?<cls>[^)]+)\) is now level (?<lvl>\d+)")]
    private static partial Regex LevelUpRe();

    [GeneratedRegex(@"^: (?<char>.+?) has been slain\.")]
    private static partial Regex DeathRe();

    [GeneratedRegex(@"^Connecting to instance server at (?<ip>[\d.]+):(?<port>\d+)")]
    private static partial Regex InstanceRe();

    [GeneratedRegex(@"^: AFK mode is now (?<state>ON|OFF)(?:\. Autoreply ""(?<reply>.*)"")?", RegexOptions.IgnoreCase)]
    private static partial Regex AfkRe();

    [GeneratedRegex(@"^: DND mode is now (?<state>ON|OFF)", RegexOptions.IgnoreCase)]
    private static partial Regex DndRe();

    // Chat channel prefixes: #global $trade %party &guild, @From/@To whisper.
    [GeneratedRegex(@"^(?<chan>[#$%&])(?<who>[^:]+): (?<text>.*)$")]
    private static partial Regex ChatRe();

    [GeneratedRegex(@"^@(?<dir>From|To) (?<who>[^:]+): (?<text>.*)$")]
    private static partial Regex WhisperRe();

    // No-prefix "Speaker: text" — NPC/boss dialogue OR local chat (ambiguous).
    [GeneratedRegex(@"^(?<who>[^:@#$%&][^:]*): (?<text>.+)$")]
    private static partial Regex SpeechRe();

    // NPC/boss speakers look like real names ("Asinia, the Praetor's Consort",
    // "The Raven", "Captain Hartlin"): letters/spaces plus , ' . - and a leading
    // capital. Rejects engine noise ("[D3D12] ...", "Error executing GEAL ...",
    // "Metadata/Monsters/...@54") via the no-digits / no-brackets constraints.
    [GeneratedRegex(@"^[A-Z][A-Za-z .,'\-]{1,47}$")]
    private static partial Regex NpcNameRe();

    // --- area classification ---
    [GeneratedRegex(@"^(?<series>[GP])(?<n>\d+)_")]
    private static partial Regex CampaignRe();

    [GeneratedRegex(@"(?:_town|_Town)$")]
    private static partial Regex TownRe();

    private static readonly string[] LeaguePrefixes =
        { "Abyss", "Chayula", "Delirium", "Expedition", "Incursion", "Sanctum" };

    /// <summary>
    /// Pretty-name overrides for area codes. Extend freely, e.g.
    /// ["G1_1"] = "The Riverbank". Empty by default — area *level* and act come
    /// straight from the log, so progress/spoiler-gating work without a name table.
    /// </summary>
    public static readonly Dictionary<string, string> Names = new();

    /// <summary>
    /// Classify an internal area code into {kind, act, difficulty, is_town, name}.
    /// kind: town | campaign | map | league | hideout | other.
    /// </summary>
    public static Dictionary<string, object?> ClassifyArea(string code)
    {
        var name = Names.TryGetValue(code, out var n) ? n : code;
        var isTown = TownRe().IsMatch(code) || code == "G_Endgame_Town";

        if (code == "G_Endgame_Town")
            return new() { ["kind"] = "town", ["act"] = null, ["difficulty"] = "endgame", ["is_town"] = true, ["name"] = name };

        var m = CampaignRe().Match(code);
        if (m.Success)
        {
            var num = int.Parse(m.Groups["n"].Value, CultureInfo.InvariantCulture);
            int? act;
            string difficulty;
            // G1-G3 = Acts 1-3 (Normal); G4-G6 = Acts 1-3 (Cruel). P-series: legacy/alt
            // campaign ids — act inferred from the digit, difficulty best-effort.
            if (m.Groups["series"].Value == "G")
            {
                act = ((num - 1) % 3) + 1;
                difficulty = num <= 3 ? "normal" : "cruel";
            }
            else
            {
                act = num;
                difficulty = "unknown";
            }
            return new()
            {
                ["kind"] = isTown ? "town" : "campaign",
                ["act"] = act,
                ["difficulty"] = difficulty,
                ["is_town"] = isTown,
                ["name"] = name,
            };
        }

        if (code.StartsWith("Map", StringComparison.Ordinal))
            return new() { ["kind"] = "map", ["act"] = null, ["difficulty"] = "endgame", ["is_town"] = false, ["name"] = name };

        if (LeaguePrefixes.Any(p => code.StartsWith(p, StringComparison.Ordinal)))
            return new() { ["kind"] = "league", ["act"] = null, ["difficulty"] = null, ["is_town"] = false, ["name"] = name };

        if (code.StartsWith("Hideout", StringComparison.Ordinal))
            return new() { ["kind"] = "hideout", ["act"] = null, ["difficulty"] = null, ["is_town"] = true, ["name"] = name };

        return new() { ["kind"] = "other", ["act"] = null, ["difficulty"] = null, ["is_town"] = isTown, ["name"] = name };
    }

    private static bool LooksLikeNpc(string speaker)
    {
        if (speaker.Any(char.IsDigit)) return false;
        if (!NpcNameRe().IsMatch(speaker)) return false;
        return speaker.Any(char.IsLower); // drop ALL-CAPS system tags
    }

    /// <summary>
    /// Parse one raw log line into a <see cref="LogEvent"/>, or null if uninteresting.
    /// <paramref name="chatSubs"/> is the set of learned message-log subsystem ids.
    /// When given, a no-prefix "Speaker: text" line is only accepted as dialogue if
    /// it came from a known message subsystem (rejects engine diagnostics that share
    /// the same shape). Pass null to skip the check (name-heuristic only).
    /// </summary>
    public static LogEvent? ParseLine(string line, IReadOnlySet<string>? chatSubs = null)
    {
        line = line.TrimEnd('\n', '\r');
        var m = LineRe().Match(line);
        if (!m.Success) return null;

        var ts = DateTime.ParseExact(m.Groups["ts"].Value, "yyyy/MM/dd HH:mm:ss",
            CultureInfo.InvariantCulture);
        var sub = m.Groups["sub"].Value;
        var msg = m.Groups["msg"].Value;

        LogEvent Mk(string kind, Dictionary<string, object?> data) => new(kind, ts, line, data, sub);

        var a = AreaRe().Match(msg);
        if (a.Success)
        {
            var info = ClassifyArea(a.Groups["code"].Value);
            info["code"] = a.Groups["code"].Value;
            info["area_level"] = int.Parse(a.Groups["lvl"].Value, CultureInfo.InvariantCulture);
            return Mk("area", info);
        }

        var lu = LevelUpRe().Match(msg);
        if (lu.Success)
            return Mk("level_up", new()
            {
                ["char"] = lu.Groups["char"].Value,
                ["cls"] = lu.Groups["cls"].Value,
                ["level"] = int.Parse(lu.Groups["lvl"].Value, CultureInfo.InvariantCulture),
            });

        var d = DeathRe().Match(msg);
        if (d.Success)
            return Mk("death", new() { ["char"] = d.Groups["char"].Value });

        var inst = InstanceRe().Match(msg);
        if (inst.Success)
            return Mk("instance", new()
            {
                ["ip"] = inst.Groups["ip"].Value,
                ["port"] = int.Parse(inst.Groups["port"].Value, CultureInfo.InvariantCulture),
            });

        var afk = AfkRe().Match(msg);
        if (afk.Success)
            return Mk("afk", new()
            {
                ["state"] = afk.Groups["state"].Value.ToUpperInvariant(),
                ["reply"] = afk.Groups["reply"].Success ? afk.Groups["reply"].Value : null,
            });

        var dnd = DndRe().Match(msg);
        if (dnd.Success)
            return Mk("dnd", new() { ["state"] = dnd.Groups["state"].Value.ToUpperInvariant() });

        var w = WhisperRe().Match(msg);
        if (w.Success)
            return Mk("whisper", new()
            {
                ["dir"] = w.Groups["dir"].Value.ToLowerInvariant(),
                ["who"] = w.Groups["who"].Value,
                ["text"] = w.Groups["text"].Value,
            });

        var c = ChatRe().Match(msg);
        if (c.Success)
        {
            var channel = c.Groups["chan"].Value switch
            {
                "#" => "global",
                "$" => "trade",
                "%" => "party",
                "&" => "guild",
                _ => "global",
            };
            return Mk("chat", new()
            {
                ["channel"] = channel,
                ["who"] = c.Groups["who"].Value,
                ["text"] = c.Groups["text"].Value,
            });
        }

        var s = SpeechRe().Match(msg);
        if (s.Success)
        {
            // No channel prefix: NPC/boss dialogue OR engine diagnostic that happens
            // to fit "X: y". Require a learned message subsystem when we have one.
            if (chatSubs is not null && !chatSubs.Contains(sub)) return null;
            var speaker = s.Groups["who"].Value;
            return Mk("dialogue", new()
            {
                ["speaker"] = speaker,
                ["text"] = s.Groups["text"].Value,
                ["likely_npc"] = LooksLikeNpc(speaker),
            });
        }

        return null;
    }
}
