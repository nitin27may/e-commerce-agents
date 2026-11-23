using Microsoft.Extensions.Logging;

namespace ECommerceAgents.Shared.Cost;

/// <summary>
/// USD per 1M tokens for one model, as the provider publishes it. <paramref name="CachedInputPer1M"/>
/// is the provider's cached-input rate; <c>null</c> means none is on file and cached tokens are billed
/// as input (the estimate then errs high, never low).
/// </summary>
public readonly record struct ModelPricing(double InputPer1M, double OutputPer1M, double? CachedInputPer1M = null);

/// <summary>The pricing file is not valid.</summary>
public sealed class PricingException(string message) : Exception(message);

/// <summary>
/// Token-to-dollar estimation, priced from <c>agents/python/config/pricing/models.yaml</c>: the same file
/// Python's <c>shared/cost.py</c> reads. <b>No price lives in code.</b> <c>PRICING_FILE</c> names another
/// file; <c>PRICING_OVERRIDES_FILE</c> names one merged over it entry by entry.
///
/// A model the file does not list has an <b>unknown</b> cost (<c>null</c>), never a guess: an earlier
/// revision priced every unknown model as gpt-4.1. A missing or invalid file is logged and treated as
/// empty, so every cost is unknown and an enforcing budget fails closed.
/// </summary>
public static class CostEstimator
{
    private static readonly object Gate = new();
    private static IReadOnlyDictionary<string, ModelPricing>? _table;
    private static IReadOnlyDictionary<string, string> _sources = new Dictionary<string, string>();
    private static int _unpricedLogged;

    /// <summary>The shipped file: the repo copy found by walking up from the binaries, else one beside them.</summary>
    public static string? DefaultPricingFile()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            var repoCopy = Path.Combine(dir.FullName, "agents", "python", "config", "pricing", "models.yaml");
            if (File.Exists(repoCopy)) return repoCopy;
            dir = dir.Parent;
        }
        var beside = Path.Combine(AppContext.BaseDirectory, "config", "pricing", "models.yaml");
        return File.Exists(beside) ? beside : null;
    }

    /// <summary>Reads and validates one pricing file. Throws <see cref="PricingException"/> or an IO exception.</summary>
    public static IReadOnlyDictionary<string, ModelPricing> LoadFile(string path) => Parse(File.ReadAllText(path));

    /// <summary>Validates a pricing document and returns its table (keys lower-cased).</summary>
    public static IReadOnlyDictionary<string, ModelPricing> Parse(string yaml)
    {
        object? doc;
        try
        {
            doc = new YamlDotNet.Serialization.DeserializerBuilder().Build().Deserialize<object?>(yaml);
        }
        catch (YamlDotNet.Core.YamlException ex)
        {
            throw new PricingException($"pricing file is not valid YAML: {ex.Message}");
        }

        if (doc is not IDictionary<object, object> root)
        {
            throw new PricingException("pricing file must be a mapping");
        }
        if (Scalar(root, "version") != "1")
        {
            throw new PricingException("unsupported pricing file version");
        }
        if (Scalar(root, "unit") is { } unit && unit != "per_1m_tokens")
        {
            throw new PricingException("unit must be per_1m_tokens");
        }

        var table = new Dictionary<string, ModelPricing>(StringComparer.Ordinal);
        if (!root.TryGetValue("models", out var modelsNode) || modelsNode is null)
        {
            return table;
        }
        if (modelsNode is not IDictionary<object, object> models)
        {
            throw new PricingException("models must be a mapping");
        }

        foreach (var (rawName, rawEntry) in models)
        {
            var name = (rawName?.ToString() ?? string.Empty).Trim().ToLowerInvariant();
            if (name.Length == 0 || rawEntry is not IDictionary<object, object> entry)
            {
                throw new PricingException($"model entry '{rawName}' is invalid");
            }
            if (!table.TryAdd(name, new ModelPricing(
                    Rate(name, entry, "input_per_1m") ?? throw new PricingException($"{name}.input_per_1m is required"),
                    Rate(name, entry, "output_per_1m") ?? throw new PricingException($"{name}.output_per_1m is required"),
                    Rate(name, entry, "cached_input_per_1m"))))
            {
                throw new PricingException($"model '{name}' is listed twice");
            }
        }
        return table;
    }

    private static string? Scalar(IDictionary<object, object> map, string key) =>
        map.TryGetValue(key, out var value) ? value?.ToString() : null;

    // YamlDotNet's untyped deserializer yields strings for scalars, so quoting is checked on the raw text:
    // a quoted number ('2') or a boolean is refused, exactly as Python refuses it.
    private static double? Rate(string model, IDictionary<object, object> entry, string field)
    {
        if (!entry.TryGetValue(field, out var raw) || raw is null)
        {
            return null;
        }
        var text = raw.ToString()!;
        if (text is "null" or "~" or "")
        {
            return null;
        }
        if (!double.TryParse(text, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var value)
            || !double.IsFinite(value) || value < 0)
        {
            throw new PricingException($"{model}.{field} must be a non-negative number, got '{text}'");
        }
        return value;
    }

    private static IReadOnlyDictionary<string, ModelPricing> Table()
    {
        lock (Gate)
        {
            if (_table is null)
            {
                var sources = new Dictionary<string, string>(StringComparer.Ordinal);
                _table = LoadConfigured(sources);
                _sources = sources;
            }
            return _table;
        }
    }

    /// <summary>
    /// <c>models.yaml#&lt;first 10 hex of the file's sha256&gt;</c>, <c>override:</c> prefixed for an override file:
    /// names the exact file content a price came from, never its directory. Twin of Python's <c>_source_label</c>.
    /// </summary>
    private static string SourceLabel(string path, bool isOverride)
    {
        var digest = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(path)))[..10].ToLowerInvariant();
        return $"{(isOverride ? "override:" : string.Empty)}{Path.GetFileName(path)}#{digest}";
    }

    private static IReadOnlyDictionary<string, ModelPricing> LoadConfigured(Dictionary<string, string> sources)
    {
        var path = Environment.GetEnvironmentVariable("PRICING_FILE");
        if (string.IsNullOrWhiteSpace(path))
        {
            path = DefaultPricingFile();
        }

        Dictionary<string, ModelPricing> table;
        try
        {
            if (path is null) throw new FileNotFoundException("pricing file not found");
            table = new Dictionary<string, ModelPricing>(LoadFile(path), StringComparer.Ordinal);
            var label = SourceLabel(path, isOverride: false);
            foreach (var key in table.Keys)
            {
                sources[key] = label;
            }
        }
        catch (Exception ex) when (ex is PricingException or IOException or UnauthorizedAccessException)
        {
            // Fixed text: the exception may quote file content. Every cost is unknown until fixed.
            Serilog.Log.Error("cost.pricing_file_invalid — every model is unpriced until it is fixed");
            return new Dictionary<string, ModelPricing>();
        }

        var overrides = Environment.GetEnvironmentVariable("PRICING_OVERRIDES_FILE");
        if (!string.IsNullOrWhiteSpace(overrides))
        {
            try
            {
                var extra = LoadFile(overrides);
                var overrideLabel = SourceLabel(overrides, isOverride: true);
                foreach (var (name, pricing) in extra)
                {
                    table[name] = pricing;
                    sources[name] = overrideLabel;
                }
            }
            catch (Exception ex) when (ex is PricingException or IOException or UnauthorizedAccessException)
            {
                Serilog.Log.Error("cost.pricing_overrides_invalid — overrides ignored");
            }
        }
        return table;
    }

    /// <summary>Forget the loaded table (tests, or after editing the file in a long-running process).</summary>
    public static void ReloadPricing()
    {
        lock (Gate)
        {
            _table = null;
            _sources = new Dictionary<string, string>();
        }
    }

    /// <summary>
    /// Which pricing file (and version of it) priced <paramref name="model"/>; null when it is unpriced.
    /// Stored beside a run's cost (<c>usage_logs.price_source</c>). Twin of Python's <c>price_source</c>.
    /// </summary>
    public static string? PriceSource(string? model)
    {
        var key = LookupKey(model);
        if (key is null)
        {
            return null;
        }
        lock (Gate)
        {
            return _sources.TryGetValue(key, out var label) ? label : null;
        }
    }

    /// <summary>
    /// Exact match, else the longest priced key the model is a dated snapshot of
    /// (<c>gpt-4.1-2025-04-14</c> prices as <c>gpt-4.1</c>; only a <c>-&lt;digit&gt;</c> suffix
    /// counts, so <c>gpt-4.1-mini</c> never resolves to <c>gpt-4.1</c>).
    /// </summary>
    public static ModelPricing? Lookup(string? model)
    {
        var key = LookupKey(model);
        return key is null ? null : Table()[key];
    }

    private static string? LookupKey(string? model)
    {
        var name = model?.Trim().ToLowerInvariant();
        if (string.IsNullOrEmpty(name))
        {
            return null;
        }

        var table = Table();
        if (table.ContainsKey(name))
        {
            return name;
        }

        foreach (var key in table.Keys.OrderByDescending(k => k.Length))
        {
            if (name.StartsWith(key, StringComparison.Ordinal)
                && name.Length > key.Length + 1
                && name[key.Length] == '-'
                && char.IsAsciiDigit(name[key.Length + 1]))
            {
                return key;
            }
        }

        return null;
    }

    /// <summary>
    /// Estimated USD for a single call, or <c>null</c> when the model is not in the pricing file. Never
    /// throws; when a logger is given, an unpriced model is reported once per process (fixed text).
    /// </summary>
    /// <param name="tokensIn">Total input, cached included, as providers report it.</param>
    /// <param name="cachedIn">The cached share of <paramref name="tokensIn"/>.</param>
    public static double? Estimate(
        string? model, int tokensIn, int tokensOut, int cachedIn = 0,
        Microsoft.Extensions.Logging.ILogger? logger = null)
    {
        var pricing = Lookup(model);
        if (pricing is null)
        {
            if (logger is not null && Interlocked.Exchange(ref _unpricedLogged, 1) == 0)
            {
                logger.LogWarning("cost.model_unpriced — cost is unknown; add the model to config/pricing/models.yaml");
            }

            return null;
        }

        var cached = Math.Clamp(cachedIn, 0, Math.Max(tokensIn, 0));
        var uncached = Math.Max(tokensIn, 0) - cached;
        var cachedRate = pricing.Value.CachedInputPer1M ?? pricing.Value.InputPer1M;
        return ((double)uncached * pricing.Value.InputPer1M
            + (double)cached * cachedRate
            + (double)Math.Max(tokensOut, 0) * pricing.Value.OutputPer1M) / 1_000_000;
    }

    /// <summary>Test hook: forget that the unpriced-model line was already logged.</summary>
    public static void ResetUnpricedLogForTests() => Interlocked.Exchange(ref _unpricedLogged, 0);

    /// <summary>
    /// Estimated USD for one run, each agent priced at the model it reported; <c>null</c> when any agent's
    /// model is unpriced (a partial sum would read as the whole).
    /// </summary>
    public static double? EstimateRun(IReadOnlyDictionary<string, ECommerceAgents.Shared.Context.SpecialistUsage> byAgent)
    {
        var total = 0d;
        foreach (var u in byAgent.Values)
        {
            var cost = Estimate(
                u.Model,
                (int)Math.Min(u.InputTokenCount, int.MaxValue),
                (int)Math.Min(u.OutputTokenCount, int.MaxValue),
                (int)Math.Min(u.CachedInputTokenCount, int.MaxValue));
            if (cost is null)
            {
                return null;
            }
            total += cost.Value;
        }
        return total;
    }

    /// <summary>What a run's <c>usage_logs</c> row records beyond token counts.</summary>
    /// <remarks>
    /// All three are <c>null</c> when nothing was captured (and the cost alone when a model is unpriced); a mode that makes no LLM call
    /// (<paramref name="noLlmCalls"/>) yields an explicit cost of 0 — so "free" and "unknown"
    /// are different rows. Twin of Python's <c>shared/usage_db.py::usage_columns</c>.
    /// </remarks>
    /// <remarks>
    /// <c>PriceSource</c> and <c>PricedAt</c> say which pricing file priced the row and when; both are null whenever
    /// no price was applied (not captured, an unpriced model, or a mode with no LLM call).
    /// </remarks>
    public readonly record struct RunColumns(
        string? Model, int? TokensCachedIn, double? CostUsd,
        string? PriceSource = null, DateTimeOffset? PricedAt = null);

    public static RunColumns SummarizeRun(ECommerceAgents.Shared.Context.RunUsage usage, bool noLlmCalls)
    {
        var byAgent = usage.ByAgent;
        if (byAgent.Count == 0)
        {
            return noLlmCalls ? new RunColumns(null, 0, 0.0) : new RunColumns(null, null, null);
        }

        var heaviest = byAgent.Values.MaxBy(u => u.InputTokenCount)!;
        var cached = (int)Math.Min(byAgent.Values.Sum(u => u.CachedInputTokenCount), int.MaxValue);
        var cost = EstimateRun(byAgent);
        var source = cost is null ? null : RunPriceSource(byAgent);
        return new RunColumns(
            string.IsNullOrEmpty(heaviest.Model) ? null : heaviest.Model, cached, cost,
            source, source is null ? null : DateTimeOffset.UtcNow);
    }

    /// <summary>Distinct pricing sources behind a run, sorted and comma-joined; null when none priced it.</summary>
    public static string? RunPriceSource(IReadOnlyDictionary<string, ECommerceAgents.Shared.Context.SpecialistUsage> byAgent)
    {
        var labels = byAgent.Values
            .Select(u => PriceSource(u.Model))
            .Where(l => l is not null)
            .Distinct(StringComparer.Ordinal)
            .OrderBy(l => l, StringComparer.Ordinal)
            .ToList();
        return labels.Count == 0 ? null : string.Join(",", labels);
    }
}
