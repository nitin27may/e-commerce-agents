// Phase 0 scaffold. Replaced in plans/dotnet-port/02-orchestrator.md.
var builder = WebApplication.CreateBuilder(args);

var app = builder.Build();

app.MapGet("/", () => Results.Ok(new { status = "scaffold", service = "orchestrator", port = 8080 }));
app.MapGet("/health", () => Results.Ok(new { healthy = true }));

app.Run("http://0.0.0.0:8080");
