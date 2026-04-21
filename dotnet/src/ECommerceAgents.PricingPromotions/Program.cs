// Phase 0 scaffold. Replaced in plans/dotnet-port/.
var builder = WebApplication.CreateBuilder(args);

var app = builder.Build();

app.MapGet("/", () => Results.Ok(new { status = "scaffold", service = "PricingPromotions", port = 8083 }));
app.MapGet("/health", () => Results.Ok(new { healthy = true }));
app.MapGet("/.well-known/agent-card.json", () => Results.Ok(new {
    name = "PricingPromotions",
    version = "0.0.0-scaffold",
    tools = Array.Empty<string>()
}));

app.Run("http://0.0.0.0:8083");
