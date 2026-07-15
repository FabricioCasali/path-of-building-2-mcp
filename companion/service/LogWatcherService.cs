using Microsoft.Extensions.Options;
using PoeCompanion.Service.Logging;

namespace PoeCompanion.Service;

/// <summary>
/// Background service that keeps <see cref="GameState"/> current. On boot it replays
/// the existing Client.txt once to reconstruct the player's frontier and past story
/// beats, then follows the file for new events.
/// </summary>
public sealed class LogWatcherService(
    GameState state,
    IOptions<CompanionOptions> options,
    ILogger<LogWatcherService> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        var path = options.Value.ClientTxtPath;
        if (string.IsNullOrWhiteSpace(path))
        {
            logger.LogWarning("Companion:ClientTxtPath não configurado — o watcher de log está ocioso. " +
                              "Aponte para o Client.txt do PoE2 em appsettings.json.");
            return;
        }

        // Seed from history so the frontier/journal reflect current progress immediately.
        if (File.Exists(path))
        {
            try
            {
                var seeded = 0;
                foreach (var ev in LogWatcher.Replay(path))
                {
                    state.Feed(ev);
                    seeded++;
                }
                logger.LogInformation("Replay inicial: {Count} eventos de {Path}.", seeded, path);
            }
            catch (Exception ex)
            {
                logger.LogError(ex, "Falha no replay inicial de {Path}.", path);
            }
        }
        else
        {
            logger.LogWarning("Client.txt não encontrado em {Path} (ainda). Vou seguir quando aparecer.", path);
        }

        // Follow new lines from the end (history already seeded above).
        var watcher = new LogWatcher(path);
        try
        {
            await watcher.RunAsync(state.Feed, fromStart: false, ct: ct).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            // normal shutdown
        }
    }
}
