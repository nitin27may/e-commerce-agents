using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Configuration;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using System.Net;
using System.Text;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Locks in audit fix #8: A2AClient must retry transient failures and
/// open a circuit breaker after sustained outages, not blow straight
/// through to a user-facing error on the first 503.
/// </summary>
public sealed class A2AClientResilienceTests
{
    private static AgentSettings DefaultSettings() => new()
    {
        AgentSharedSecret = new string('s', 48),
        JwtSecret = new string('j', 48),
        Environment = "test",
    };

    private static A2AClient BuildClient(QueueingHandler handler) =>
        new(
            new HttpClient(handler) { BaseAddress = new Uri("http://localhost") },
            DefaultSettings(),
            NullLogger<A2AClient>.Instance
        );

    [Fact]
    public async Task SendAsync_RetriesTransient5xxAndSucceedsOnRecovery()
    {
        var handler = new QueueingHandler();
        handler.Enqueue(HttpStatusCode.ServiceUnavailable);
        handler.Enqueue(HttpStatusCode.BadGateway);
        handler.EnqueueOk("recovered");

        var client = BuildClient(handler);
        var reply = await client.SendAsync(
            "test-agent",
            "http://localhost",
            "hello"
        );

        reply.Should().Be("recovered");
        handler.CallCount.Should().Be(3);
    }

    [Fact]
    public async Task SendAsync_RetriesOnHttpRequestExceptionThenSucceeds()
    {
        var handler = new QueueingHandler();
        handler.EnqueueException(new HttpRequestException("connection reset"));
        handler.EnqueueOk("recovered");

        var client = BuildClient(handler);
        var reply = await client.SendAsync("t", "http://localhost", "hi");

        reply.Should().Be("recovered");
        handler.CallCount.Should().Be(2);
    }

    [Fact]
    public async Task SendAsync_DoesNotRetryOn4xxOtherThan408_429()
    {
        var handler = new QueueingHandler();
        handler.Enqueue(HttpStatusCode.NotFound);
        handler.EnqueueOk("never reached");

        var client = BuildClient(handler);
        var reply = await client.SendAsync("t", "http://localhost", "hi");

        reply.Should().Contain("status 404");
        handler.CallCount.Should().Be(1);
    }

    [Fact]
    public async Task SendAsync_GivesUpAfterMaxRetriesAndReturnsErrorMessage()
    {
        var handler = new QueueingHandler();
        for (var i = 0; i < 6; i++)
        {
            handler.Enqueue(HttpStatusCode.ServiceUnavailable);
        }

        var client = BuildClient(handler);
        var reply = await client.SendAsync("t", "http://localhost", "hi");

        reply.Should().Contain("status 503");
        // 1 initial + 3 retries
        handler.CallCount.Should().Be(4);
    }

    [Fact]
    public async Task SendAsync_OpensCircuitAfterRepeatedFailures()
    {
        var handler = new QueueingHandler();
        // Polly's circuit needs MinimumThroughput=5 transient outcomes
        // inside one sampling window before it can open. Two SendAsync
        // calls × 4 attempts each = 8 transient outcomes — comfortably
        // over the threshold.
        for (var i = 0; i < 12; i++)
        {
            handler.Enqueue(HttpStatusCode.InternalServerError);
        }

        var client = BuildClient(handler);
        await client.SendAsync("t", "http://localhost", "hi");
        await client.SendAsync("t", "http://localhost", "hi");
        var beforeShortCircuitCalls = handler.CallCount;

        // Subsequent call should short-circuit: the circuit-open path
        // returns the "temporarily unavailable" copy without an HTTP
        // attempt.
        handler.EnqueueOk("would have worked");
        var reply = await client.SendAsync("t", "http://localhost", "hi");

        reply.Should().Contain("temporarily unavailable");
        handler.CallCount.Should().Be(beforeShortCircuitCalls); // no extra call
    }

    /// <summary>
    /// Test-only HttpMessageHandler. Each SendAsync call dequeues the
    /// next scripted response — either a status code or an exception.
    /// </summary>
    private sealed class QueueingHandler : HttpMessageHandler
    {
        private readonly Queue<Func<HttpResponseMessage>> _queue = new();
        public int CallCount { get; private set; }

        public void Enqueue(HttpStatusCode status, string body = "")
        {
            _queue.Enqueue(() => new HttpResponseMessage(status)
            {
                Content = new StringContent(body, Encoding.UTF8, "application/json"),
            });
        }

        public void EnqueueOk(string responseText)
        {
            _queue.Enqueue(() => new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(
                    $"{{\"response\":\"{responseText}\"}}",
                    Encoding.UTF8,
                    "application/json"
                ),
            });
        }

        public void EnqueueException(Exception ex)
        {
            _queue.Enqueue(() => throw ex);
        }

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken
        )
        {
            CallCount++;
            if (_queue.Count == 0)
            {
                throw new InvalidOperationException("no more scripted responses");
            }

            var make = _queue.Dequeue();
            try
            {
                return Task.FromResult(make());
            }
            catch (Exception ex)
            {
                return Task.FromException<HttpResponseMessage>(ex);
            }
        }
    }
}
