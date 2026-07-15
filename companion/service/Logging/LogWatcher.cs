namespace PoeCompanion.Service.Logging;

/// <summary>
/// Follows Client.txt and turns appended lines into <see cref="LogEvent"/>s.
/// Port of <c>tail()</c>/<c>replay()</c> in <c>pob_mcp/logwatch.py</c>.
///
/// Like the Python version it learns which subsystem hex ids carry chat/message
/// lines (from unambiguous message kinds) so NPC dialogue can be told apart from
/// engine diagnostics that share the "Speaker: text" shape.
/// </summary>
public sealed class LogWatcher(string path)
{
    private readonly HashSet<string> _chatSubs = new();

    /// <summary>The subsystem ids learned so far (message-log carriers).</summary>
    public IReadOnlySet<string> ChatSubs => _chatSubs;

    /// <summary>
    /// Follow the file forever, invoking <paramref name="onEvent"/> per parsed event.
    /// Survives the game rewriting/rotating the file (detected as the size shrinking)
    /// by reopening from the top. <paramref name="fromStart"/> replays existing
    /// content first, then follows.
    /// </summary>
    public async Task RunAsync(Action<LogEvent> onEvent, bool fromStart = false,
        TimeSpan? poll = null, CancellationToken ct = default)
    {
        var pollDelay = poll ?? TimeSpan.FromSeconds(1);

        while (!ct.IsCancellationRequested)
        {
            FileStream fs;
            try
            {
                // FileShare.ReadWrite: the game keeps the file open for writing.
                fs = new FileStream(path, FileMode.Open, FileAccess.Read,
                    FileShare.ReadWrite | FileShare.Delete);
            }
            catch (FileNotFoundException)
            {
                await Task.Delay(pollDelay, ct).ConfigureAwait(false);
                continue;
            }
            catch (DirectoryNotFoundException)
            {
                await Task.Delay(pollDelay, ct).ConfigureAwait(false);
                continue;
            }

            await using (fs)
            using (var reader = new StreamReader(fs))
            {
                if (!fromStart)
                    fs.Seek(0, SeekOrigin.End); // only new lines from here

                while (!ct.IsCancellationRequested)
                {
                    var line = await reader.ReadLineAsync(ct).ConfigureAwait(false);
                    if (line is not null)
                    {
                        var ev = LogParser.ParseLine(line, _chatSubs);
                        if (ev is not null)
                        {
                            if (LogParser.MessageKinds.Contains(ev.Kind))
                                _chatSubs.Add(ev.Sub); // learn the message subsystem
                            onEvent(ev);
                        }
                        continue;
                    }

                    // No new data: check for truncation/rotation, else wait.
                    await Task.Delay(pollDelay, ct).ConfigureAwait(false);
                    try
                    {
                        if (fs.Position > new FileInfo(path).Length)
                            break; // file shrank -> reopen from the top
                    }
                    catch (IOException)
                    {
                        break;
                    }
                }
            }
        }
    }

    /// <summary>
    /// Parse the whole existing log once (no following). Two-pass: learn the message
    /// subsystem first so NPC dialogue is separated from engine diagnostics even for
    /// the earliest lines. Mirrors <c>replay()</c> in logwatch.py.
    /// </summary>
    public static IEnumerable<LogEvent> Replay(string path)
    {
        var chatSubs = CollectChatSubs(path);
        foreach (var line in ReadLinesShared(path))
        {
            var ev = LogParser.ParseLine(line, chatSubs);
            if (ev is not null) yield return ev;
        }
    }

    /// <summary>First pass: learn which subsystem ids carry chat/message lines.</summary>
    private static HashSet<string> CollectChatSubs(string path)
    {
        var subs = new HashSet<string>();
        foreach (var line in ReadLinesShared(path))
        {
            var ev = LogParser.ParseLine(line); // no gating; we only want message-kind subs
            if (ev is not null && LogParser.MessageKinds.Contains(ev.Kind))
                subs.Add(ev.Sub);
        }
        return subs;
    }

    // Read a file line-by-line with permissive sharing — the game keeps Client.txt
    // open for writing while running, so the default FileShare.Read would throw.
    private static IEnumerable<string> ReadLinesShared(string path)
    {
        using var fs = new FileStream(path, FileMode.Open, FileAccess.Read,
            FileShare.ReadWrite | FileShare.Delete);
        using var reader = new StreamReader(fs);
        string? line;
        while ((line = reader.ReadLine()) is not null)
            yield return line;
    }
}
