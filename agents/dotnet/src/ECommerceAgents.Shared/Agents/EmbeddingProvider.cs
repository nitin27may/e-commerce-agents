using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using ECommerceAgents.Shared.Configuration;
using OpenAI.Embeddings;

namespace ECommerceAgents.Shared.Agents;

/// <summary>
/// The seam that lets embeddings have a replay provider, the way
/// <see cref="ChatClientFactory"/> does for chat (#52's .NET twin).
/// </summary>
/// <remarks>
/// <c>EmbeddingClient</c> is a sealed OpenAI SDK type, so there was nowhere to
/// slot an offline implementation — and unlike Python, where the equivalent gap
/// produced a caught tool error, on .NET it was a **startup crash**:
/// product-discovery resolves an embedding client in its DI graph, so under
/// <c>LLM_PROVIDER=replay</c> the whole service failed to boot with
/// "OPENAI_API_KEY is required when LLM_PROVIDER=openai". That made a
/// deterministic .NET eval suite impossible, which is why it is fixed here
/// rather than worked around in the runner.
/// </remarks>
public interface IEmbeddingProvider
{
    /// <summary>Embeds <paramref name="text"/> as a pgvector literal.</summary>
    Task<string> EmbedAsVectorLiteralAsync(string text, CancellationToken ct = default);
}

/// <summary>
/// Stand-in used when no real embedding client could be built (no API key, no
/// Azure endpoint). Every call throws, which <c>ProductTools</c> turns into the
/// full-text-only fallback — the .NET twin of Python, where the same missing key
/// raises inside <c>semantic_search</c> and is caught there. Without it the
/// service refused to start at all, a worse outcome than degraded search.
/// </summary>
public sealed class UnavailableEmbeddingProvider(string reason) : IEmbeddingProvider
{
    public string Reason { get; } = reason;

    public Task<string> EmbedAsVectorLiteralAsync(string text, CancellationToken ct = default) =>
        throw new InvalidOperationException($"Embeddings are unavailable: {Reason}");
}

/// <summary>Real embeddings from OpenAI, Azure OpenAI or an OpenAI-compatible server.</summary>
/// <param name="client">The SDK embedding client.</param>
/// <param name="requestDimensions">Sent as the <c>dimensions</c> parameter (text-embedding-3 models only); null omits it.</param>
/// <param name="expectedDimensions">
/// The width the database column has (<c>EMBEDDING_DIM</c>). A vector of any other size is refused here with a
/// message, rather than reaching pgvector as an opaque error. Null skips the check (tests, no configured width).
/// </param>
public sealed class OpenAIEmbeddingProvider(
    EmbeddingClient client,
    int? requestDimensions = null,
    int? expectedDimensions = null) : IEmbeddingProvider
{
    public async Task<string> EmbedAsVectorLiteralAsync(string text, CancellationToken ct = default)
    {
        var options = requestDimensions is { } d ? new EmbeddingGenerationOptions { Dimensions = d } : null;
        var response = await client.GenerateEmbeddingAsync(text, options, ct);
        var vector = response.Value.ToFloats().ToArray();
        if (expectedDimensions is { } expected && vector.Length != expected)
        {
            throw new InvalidOperationException(
                $"the embedding model returned {vector.Length} dimensions but EMBEDDING_DIM={expected}; set EMBEDDING_DIM to the "
                + "model's size and run scripts/reset_embeddings.py --dim N, or pick a model that supports dimensions=");
        }

        return ToVectorLiteral(vector.Select(f => (double)f));
    }

    internal static string ToVectorLiteral(IEnumerable<double> values) =>
        "[" + string.Join(",", values.Select(v => v.ToString(CultureInfo.InvariantCulture))) + "]";
}

/// <summary>
/// Deterministic offline embeddings for <c>LLM_PROVIDER=replay</c>.
/// </summary>
/// <remarks>
/// A hashing vectorizer: tokens are hashed into buckets with a sign and summed,
/// then L2-normalised, so texts sharing words land close under cosine distance.
/// Real vector search over a real index — deterministic, free, offline — but
/// **not** a semantic model: it has no notion of synonymy.
///
/// <b>This must stay byte-identical to Python's
/// <c>shared/replay_embeddings.py::embed_text</c>.</b> Both stacks read the same
/// <c>product_embeddings</c> rows, and those rows are written by
/// <c>scripts/generate_embeddings.py</c> — so if the two bucketing schemes ever
/// diverge, .NET queries score noise against Python-written vectors and nothing
/// errors to say so. That shared constraint is why both use SHA-256: it is in
/// both standard libraries, where BLAKE2b (Python's first choice here) is not
/// available in .NET without a third-party package.
/// </remarks>
public sealed class ReplayEmbeddingProvider(int dimensions = ReplayEmbeddingProvider.Dimensions) : IEmbeddingProvider
{
    /// <summary>
    /// The default width: matches text-embedding-3-small, so replay vectors drop into the same column.
    /// <c>EMBEDDING_DIM</c> overrides it (Python's <c>embed_text(text, dimensions)</c> twin).
    /// </summary>
    public const int Dimensions = 1536;

    public Task<string> EmbedAsVectorLiteralAsync(string text, CancellationToken ct = default) =>
        Task.FromResult(OpenAIEmbeddingProvider.ToVectorLiteral(Embed(text, dimensions)));

    /// <summary>The raw vector. Public because it is a cross-stack contract, not an
    /// implementation detail: tests assert it byte-for-byte against Python's output.</summary>
    public static double[] Embed(string text, int dimensions = Dimensions)
    {
        // `double`, not `float`, so this matches Python bit for bit. Python
        // computes in float64; accumulating and normalising in float32 here
        // produced identical buckets and signs but values that differed in the
        // 8th decimal (0.37796450 vs 0.37796447). Harmless for ranking, but it
        // would make a cross-stack equality test impossible to write honestly —
        // and "compatible within a tolerance" is a weaker guarantee than this
        // shared-database invariant deserves.
        var vector = new double[dimensions];
        foreach (var token in Tokenize(text))
        {
            var (index, sign) = Bucket(token, dimensions);
            vector[index] += sign;
        }

        var norm = Math.Sqrt(vector.Sum(v => v * v));
        if (norm == 0d)
        {
            // Empty or punctuation-only input. A zero vector leaves cosine
            // distance undefined and pgvector orders on NaN, so anchor it.
            vector[0] = 1d;
            return vector;
        }

        for (var i = 0; i < vector.Length; i++)
        {
            vector[i] /= norm;
        }
        return vector;
    }

    /// <summary>Lowercase alphanumeric runs — the same tokens Python's regex yields.</summary>
    private static IEnumerable<string> Tokenize(string text)
    {
        var token = new StringBuilder();
        foreach (var ch in text)
        {
            if (char.IsAsciiLetterOrDigit(ch))
            {
                token.Append(char.ToLowerInvariant(ch));
                continue;
            }
            if (token.Length > 0)
            {
                yield return token.ToString();
                token.Clear();
            }
        }
        if (token.Length > 0)
        {
            yield return token.ToString();
        }
    }

    /// <summary>
    /// Token to (bucket, signed weight). The sign comes from a different byte of
    /// the same digest than the index, so unrelated tokens colliding in one
    /// bucket tend to cancel rather than reinforce.
    /// </summary>
    private static (int Index, double Sign) Bucket(string token, int dimensions)
    {
        var digest = SHA256.HashData(Encoding.UTF8.GetBytes(token));
        var index = (int)(System.Buffers.Binary.BinaryPrimitives.ReadUInt32BigEndian(digest) % (uint)dimensions);
        var sign = (digest[4] & 1) == 1 ? 1d : -1d;
        return (index, sign);
    }
}
