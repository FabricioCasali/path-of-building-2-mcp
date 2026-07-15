namespace PoeCompanion.Service.Lore;

/// <summary>
/// A story beat the player literally saw in-game (a boss/NPC dialogue line).
/// Port of the <c>Beat</c> dataclass in <c>pob_mcp/lore.py</c>.
/// </summary>
public sealed record Beat(string Speaker, string Text, DateTime Ts, string FrontierLabel)
{
    /// <summary>Dedup key: same speaker + same line = same beat.</summary>
    public (string, string) Key() => (Speaker, Text);
}
