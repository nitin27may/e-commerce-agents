using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using FluentAssertions;
using System.Security.Cryptography;
using System.Text;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Deterministic offline embeddings, and the cross-stack invariant they depend on.
/// </summary>
/// <remarks>
/// Without a replay path here, product-discovery <b>failed to start</b> under
/// <c>LLM_PROVIDER=replay</c> — its DI graph resolves an embedding client, and a
/// sealed SDK type has no offline implementation, so the service died with
/// "OPENAI_API_KEY is required when LLM_PROVIDER=openai" before binding a port.
/// That made a deterministic .NET eval suite impossible.
/// </remarks>
public sealed class ReplayEmbeddingProviderTests
{
    /// <summary>
    /// SHA-256 of the comma-joined "F8"-formatted vector, computed from
    /// <c>agents/python/shared/replay_embeddings.py::embed_text</c>.
    /// </summary>
    /// <remarks>
    /// These are the whole point of the file. Both stacks read the same
    /// <c>product_embeddings</c> rows, and those rows are written by the Python
    /// seeding script — so if the two bucketing schemes diverge, .NET queries
    /// score noise against Python-written vectors and <b>nothing errors to say
    /// so</b>. Hard-coding Python's digests turns that silent failure into a
    /// build break.
    /// </remarks>
    [Theory]
    [InlineData("Sony WH-1000XM5 | Premium wireless noise-cancelling headphones with 30-hour battery", 12, "0245B8A828B95C35")]
    [InlineData("wireless noise cancelling headphones", 4, "E18D0E53B67AFF06")]
    [InlineData("", 1, "D0751BA3FAAB5A28")]
    [InlineData("!!! --- ???", 1, "D0751BA3FAAB5A28")]
    [InlineData("Hoka Clifton 9 | Lightweight cushioned running shoes", 7, "54468A2C429AE37B")]
    [InlineData("Breville Barista Express espresso machine with conical burr grinder", 9, "B473D9106A7AA54C")]
    public void VectorsAreBitIdenticalToPython(string text, int nonZero, string expectedDigest)
    {
        var vector = ReplayEmbeddingProvider.Embed(text);

        vector.Should().HaveCount(ReplayEmbeddingProvider.Dimensions);
        vector.Count(v => v != 0d).Should().Be(nonZero);
        Digest(vector).Should().Be(
            expectedDigest,
            "Python's shared/replay_embeddings.py must produce the identical vector — "
                + "both stacks read the same product_embeddings rows");
    }

    [Fact]
    public void RelatedTextScoresHigherThanUnrelated()
    {
        var product = ReplayEmbeddingProvider.Embed("Sony WH-1000XM5 | Premium wireless noise-cancelling headphones");
        var related = ReplayEmbeddingProvider.Embed("wireless noise cancelling headphones");
        var unrelated = ReplayEmbeddingProvider.Embed("stainless steel kitchen blender");

        Cosine(product, related).Should().BeGreaterThan(0.3);
        Cosine(product, related).Should().BeGreaterThan(Cosine(product, unrelated));
    }

    [Fact]
    public void EveryVectorIsUnitLength_IncludingDegenerateInput()
    {
        // A zero vector leaves cosine distance undefined and pgvector orders on NaN.
        foreach (var text in new[] { "headphones", "", "!!!  ---  ???" })
        {
            Math.Sqrt(ReplayEmbeddingProvider.Embed(text).Sum(v => v * v)).Should().BeApproximately(1.0, 1e-9);
        }
    }

    [Fact]
    public async Task TheProviderEmitsAPgvectorLiteral()
    {
        var literal = await new ReplayEmbeddingProvider().EmbedAsVectorLiteralAsync("headphones");

        literal.Should().StartWith("[").And.EndWith("]");
        literal.Split(',').Should().HaveCount(ReplayEmbeddingProvider.Dimensions);
    }

    [Fact]
    public void TheFactoryReturnsTheReplayProviderWithoutCredentials()
    {
        // The actual fix: this used to throw before the service could start.
        var provider = EmbeddingClientFactory.CreateProvider(
            new AgentSettings { LlmProvider = "replay", OpenAiApiKey = "" });

        provider.Should().BeOfType<ReplayEmbeddingProvider>();
    }

    private static double Cosine(double[] a, double[] b) => a.Zip(b, (x, y) => x * y).Sum();

    private static string Digest(double[] vector)
    {
        var joined = string.Join(",", vector.Select(v => v.ToString("F8")));
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(joined)))[..16];
    }
}
