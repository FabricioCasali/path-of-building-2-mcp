using System.Net.WebSockets;
using Microsoft.Extensions.Options;
using PoeCompanion.Service;
using PoeCompanion.Service.Api;
using PoeCompanion.Service.Llm;
using PoeCompanion.Service.Lore;

var builder = WebApplication.CreateBuilder(args);

builder.Services.Configure<CompanionOptions>(builder.Configuration.GetSection(CompanionOptions.SectionName));

// LoreGate seeded with the editable system prompt (falls back to the built-in default).
builder.Services.AddSingleton<GameState>(sp =>
{
    var opts = sp.GetRequiredService<IOptions<CompanionOptions>>().Value;
    string? systemOverride = null;
    var promptPath = opts.SystemPromptPath;
    if (!string.IsNullOrWhiteSpace(promptPath) && File.Exists(promptPath))
        systemOverride = File.ReadAllText(promptPath).Trim();
    return new GameState(new LoreGate(systemOverride), new Chronicle());
});

builder.Services.AddSingleton<ILlmProvider>(sp =>
{
    var opts = sp.GetRequiredService<IOptions<CompanionOptions>>().Value;
    return new ClaudeCliProvider(opts.ClaudeExecutable);
});

builder.Services.AddHostedService<LogWatcherService>();

var app = builder.Build();

app.UseWebSockets();

app.MapGet("/health", (GameState state) =>
{
    var snap = state.Snapshot();
    return Results.Ok(new { ok = true, frontier = snap.Frontier, characters = snap.Characters });
});

app.Map("/ws", async (HttpContext ctx, GameState state, ILlmProvider llm,
    IOptions<CompanionOptions> options, ILoggerFactory loggerFactory) =>
{
    if (!ctx.WebSockets.IsWebSocketRequest)
    {
        ctx.Response.StatusCode = StatusCodes.Status400BadRequest;
        return;
    }
    using var socket = await ctx.WebSockets.AcceptWebSocketAsync();
    var logger = loggerFactory.CreateLogger("CompanionSocket");
    await CompanionSocket.HandleAsync(socket, state, llm, options.Value, logger, ctx.RequestAborted);
});

app.Run();
