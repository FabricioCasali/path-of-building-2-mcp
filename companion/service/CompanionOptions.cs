namespace PoeCompanion.Service;

/// <summary>App configuration, bound from the "Companion" section of appsettings.json
/// (env vars override, e.g. Companion__ClientTxtPath).</summary>
public sealed class CompanionOptions
{
    public const string SectionName = "Companion";

    /// <summary>Full path to Path of Exile 2's Client.txt. Required for the log watcher.</summary>
    public string ClientTxtPath { get; set; } = "";

    /// <summary>Model alias passed to the CLI (sonnet | haiku | opus | fable).</summary>
    public string Model { get; set; } = "sonnet";

    /// <summary>Path to the editable lore system prompt; falls back to the built-in default.</summary>
    public string SystemPromptPath { get; set; } = Path.Combine("prompts", "lore-system.txt");

    /// <summary>Executable name/path for the Claude Code CLI.</summary>
    public string ClaudeExecutable { get; set; } = "claude";
}
