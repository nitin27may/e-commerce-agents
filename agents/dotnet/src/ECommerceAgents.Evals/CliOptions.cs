namespace ECommerceAgents.Evals;

/// <summary>
/// Hand-rolled argument parsing — four flags do not justify a new central package
/// version. Flag names mirror Python's <c>run_evals.py</c> so the two are muscle-memory
/// compatible.
/// </summary>
internal sealed record CliOptions(
    string Agent,
    string DatasetPath,
    double PassThreshold,
    string? BaselinePath,
    string? UpdateBaseline,
    double MaxRegression,
    string? OutputJson,
    bool Verbose
)
{
    public static CliOptions? Parse(string[] args)
    {
        string? agent = null, dataset = null, baseline = null, update = null, outputJson = null;
        double threshold = 0.7, maxRegression = 0.05;
        var verbose = false;

        for (var i = 0; i < args.Length; i++)
        {
            string Next() => i + 1 < args.Length ? args[++i] : "";
            switch (args[i])
            {
                case "--agent": agent = Next(); break;
                case "--dataset": dataset = Next(); break;
                case "--baseline": baseline = Next(); break;
                case "--update-baseline": update = Next(); break;
                case "--output-json": outputJson = Next(); break;
                case "--pass-threshold": double.TryParse(Next(), out threshold); break;
                case "--max-regression": double.TryParse(Next(), out maxRegression); break;
                case "--verbose" or "-v": verbose = true; break;
                case "--help" or "-h": return null;
                default:
                    Console.Error.WriteLine($"Unknown option: {args[i]}");
                    return null;
            }
        }

        if (agent is null || dataset is null)
        {
            Console.Error.WriteLine("--agent and --dataset are required.");
            return null;
        }

        return new CliOptions(agent, dataset, threshold, baseline, update, maxRegression, outputJson, verbose);
    }

    public static void PrintUsage() => Console.Error.WriteLine("""
        Usage: dotnet run --project src/ECommerceAgents.Evals -- [options]

          --agent <name>              Agent under evaluation (required)
          --dataset <path>            Dataset JSON (required)
          --pass-threshold <0..1>     Overall score needed to exit 0 (default 0.7)
          --baseline <path>           Compare against a committed baseline
          --max-regression <0..1>     Tolerated drop per dimension (default 0.05)
          --update-baseline <path>    Write this run's scores as the new baseline
          --output-json <path>        Write the full summary
          --verbose, -v               Per-case detail

        Set LLM_PROVIDER=replay for a deterministic, credential-free run.
        """);
}
