namespace PoeCompanion.Service.Logging;

/// <summary>
/// One parsed line of Path of Exile 2's Client.txt. Faithful port of the
/// <c>Event</c> dataclass in <c>pob_mcp/logwatch.py</c>. <see cref="Kind"/>
/// drives everything downstream (LoreGate, and later the advisor).
/// </summary>
/// <param name="Kind">
/// One of: area, level_up, death, instance, afk, dnd, whisper, chat, dialogue.
/// </param>
/// <param name="Ts">Timestamp parsed from the line.</param>
/// <param name="Raw">The original raw line.</param>
/// <param name="Data">Kind-specific fields (mirrors the Python <c>data</c> dict).</param>
/// <param name="Sub">Subsystem hex id of the source line.</param>
public sealed record LogEvent(
    string Kind,
    DateTime Ts,
    string Raw,
    IReadOnlyDictionary<string, object?> Data,
    string Sub)
{
    /// <summary>Convenience typed getter for a data field.</summary>
    public T? Get<T>(string key) => Data.TryGetValue(key, out var v) && v is T t ? t : default;

    public string? GetString(string key) => Data.TryGetValue(key, out var v) ? v as string : null;
}
