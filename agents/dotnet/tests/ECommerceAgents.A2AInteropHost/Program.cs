using System.Runtime.InteropServices;
using ECommerceAgents.TestFixtures;

// Usage: A2AInteropHost [url] [mode]
//   url   where to listen (default http://127.0.0.1:18081)
//   mode  the scripted model's behaviour: ok (default), boom, miss, hold, weird
// Prints "listening <url>" once the host accepts connections, then runs until it is stopped (SIGINT or SIGTERM).
var url = args.Length > 0 ? args[0] : "http://127.0.0.1:18081";
var mode = args.Length > 1 ? args[1] : "ok";

using var stop = new CancellationTokenSource();
using var sigint = PosixSignalRegistration.Create(PosixSignal.SIGINT, context =>
{
    context.Cancel = true;
    stop.Cancel();
});
using var sigterm = PosixSignalRegistration.Create(PosixSignal.SIGTERM, context =>
{
    context.Cancel = true;
    stop.Cancel();
});

await using var specialist = await ScriptedSpecialist.StartAsync(mode, realSocket: true, urls: url);
Console.WriteLine($"listening {specialist.BaseUrl}");

try
{
    await Task.Delay(Timeout.Infinite, stop.Token);
}
catch (OperationCanceledException)
{
    // shutting down
}
