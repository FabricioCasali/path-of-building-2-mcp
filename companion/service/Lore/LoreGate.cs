using System.Text;
using PoeCompanion.Service.Logging;

namespace PoeCompanion.Service.Lore;

/// <summary>A spoiler-safe prompt bundle for an LLM to answer a lore question.</summary>
public sealed record LorePrompt(string System, string Context, string Question, string Frontier);

/// <summary>
/// Tracks the spoiler frontier and witnessed story beats, and builds gated prompts.
/// Faithful port of <c>LoreGate</c> in <c>pob_mcp/lore.py</c>.
///
/// The LLM supplies the lore; this class constrains *how much* it may reveal:
/// only up to the frontier the player has actually reached, anchored on the beats
/// they have literally witnessed.
/// </summary>
public sealed class LoreGate
{
    // The default system prompt, verbatim from lore.py so the C# port produces the
    // same gated prompt as the Python oracle. The service may override it by loading
    // prompts/lore-system.txt (see LoreGate(systemPromptOverride)).
    public const string DefaultSystem =
        "Você é um lore-master de Path of Exile 2 que responde SEM SPOILER. " +
        "Regra absoluta: só revele elementos da história ATÉ a fronteira de progresso " +
        "informada. Se a pergunta exigir algo além dela, diga que está adiante do ponto " +
        "atual do jogador e ofereça só o que é seguro, sem entregar o que vem depois. " +
        "Prefira ancorar a resposta no que o jogador comprovadamente já viu.";

    private readonly string _system;
    private readonly List<Beat> _beats = new();
    private readonly HashSet<(string, string)> _seen = new();
    private readonly Dictionary<string, int> _bosses = new(); // speaker -> times heard

    public Frontier Frontier { get; private set; } = new();

    public LoreGate(string? systemPromptOverride = null)
    {
        _system = string.IsNullOrWhiteSpace(systemPromptOverride) ? DefaultSystem : systemPromptOverride;
    }

    public void Feed(LogEvent ev)
    {
        var d = ev.Data;
        if (ev.Kind == "area")
        {
            var cand = new Frontier(
                Difficulty: d.GetValueOrDefault("difficulty") as string ?? "normal",
                Act: IntOr(d, "act", Frontier.Act),
                AreaLevel: IntOr(d, "area_level", Frontier.AreaLevel),
                AreaCode: d.GetValueOrDefault("code") as string,
                Kind: d.GetValueOrDefault("kind") as string);

            // Only advance the ceiling; backtracking to town never lowers it.
            if (cand.IsAheadOf(Frontier))
            {
                Frontier = cand;
            }
            else
            {
                // Keep latest area_code/kind for context without moving the key.
                Frontier = Frontier with
                {
                    AreaCode = d.GetValueOrDefault("code") as string,
                    Kind = d.GetValueOrDefault("kind") as string ?? Frontier.Kind,
                };
            }
        }
        else if (ev.Kind == "dialogue" && d.GetValueOrDefault("likely_npc") is true)
        {
            var speaker = (string)d["speaker"]!;
            var text = (string)d["text"]!;
            var beat = new Beat(speaker, text, ev.Ts, Frontier.Label());
            _bosses[speaker] = _bosses.GetValueOrDefault(speaker) + 1;
            if (_seen.Add(beat.Key()))
                _beats.Add(beat);
        }
    }

    // Python's `d.get(key) or fallback`: null OR 0 falls back.
    private static int IntOr(IReadOnlyDictionary<string, object?> d, string key, int fallback)
    {
        if (d.TryGetValue(key, out var v) && v is int i && i != 0) return i;
        return fallback;
    }

    /// <summary>Distinct NPC/boss speakers the player has heard, most-heard first.</summary>
    public IReadOnlyList<string> CharactersMet() =>
        _bosses.OrderByDescending(kv => kv.Value).Select(kv => kv.Key).ToList();

    public IReadOnlyList<Beat> Journal(int limit = 15) =>
        _beats.Count <= limit ? _beats.ToList() : _beats.GetRange(_beats.Count - limit, limit);

    public string BoundaryText()
    {
        var met = CharactersMet();
        var metStr = met.Count > 0 ? string.Join(", ", met.Take(20)) : "(nenhum registrado no log)";
        return $"FRONTEIRA DE PROGRESSO DO JOGADOR: {Frontier.Label()}.\n" +
               $"PERSONAGENS/BOSSES QUE O JOGADOR JÁ ENCONTROU (fala capturada no log): {metStr}.";
    }

    /// <summary>Build a spoiler-safe prompt bundle for the LLM to answer <paramref name="question"/>.</summary>
    public LorePrompt BuildPrompt(string question)
    {
        var recent = _beats.Count <= 8 ? _beats : _beats.GetRange(_beats.Count - 8, 8);
        string journal;
        if (recent.Count == 0)
        {
            journal = "  (sem falas de NPC capturadas ainda)";
        }
        else
        {
            var sb = new StringBuilder();
            for (var i = 0; i < recent.Count; i++)
            {
                if (i > 0) sb.Append('\n');
                var b = recent[i];
                sb.Append($"  - [{b.FrontierLabel}] {b.Speaker}: {b.Text}");
            }
            journal = sb.ToString();
        }

        var context = $"{BoundaryText()}\n\n" +
                      $"BEATS RECENTES QUE O JOGADOR VIU (mais recentes):\n{journal}";
        return new LorePrompt(_system, context, question, Frontier.Label());
    }
}
