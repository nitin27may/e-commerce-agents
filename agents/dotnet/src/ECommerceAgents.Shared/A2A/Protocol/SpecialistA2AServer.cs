using System.Runtime.CompilerServices;
using A2A;
using AmbientContext = ECommerceAgents.Shared.Context.RequestContext;
using ECommerceAgents.Shared.Logging;
using Microsoft.Extensions.Logging;

namespace ECommerceAgents.Shared.A2A.Protocol;

/// <summary>
/// The SDK's <see cref="A2AServer"/> with an admission pre-check and error hygiene. Twin of Python's
/// <c>_SpecialistRequestHandler</c>.
/// </summary>
/// <remarks>
/// <list type="bullet">
/// <item><c>SendMessage</c> / <c>SendStreamingMessage</c> over the active-task cap answer a JSON-RPC
/// error (<c>-32603</c>: the spec defines no "busy" code) before any task exists.</item>
/// <item>An exception that is not an <see cref="A2AException"/> (a failing task store, a bug in the
/// handler) is logged here and answered with the fixed <see cref="A2AWire.InternalErrorText"/>, so an
/// exception's own text (a table, a path) never reaches the wire. The SDK's endpoint already masks the
/// generic case; this also puts the detail in the log through <see cref="LogSafe"/>.</item>
/// </list>
/// Task reads are owner-scoped in <see cref="OwnedBoundedTaskStore"/>, which every read path (get, list,
/// cancel, subscribe, and continuing a task by id in a message) goes through.
/// <para>
/// <b>Anonymous callers.</b> A caller with no resolved user (an unauthenticated chat forwards an empty email; a
/// health/system call forwards nothing, which the auth middleware stamps <c>system</c>) shares one task owner, so
/// owner scoping cannot tell two of them apart. Such a caller may touch no stored task: get and subscribe answer
/// "task not found", list answers empty, and a message cannot continue a task by id. The task its own response
/// stream returns is all it needs. <c>CancelTask</c> stays open to them: the client's best-effort cancel of a
/// stream it abandoned is a legitimate anonymous call, and it needs the task's id, which only that stream's own
/// response carries. The per-process active-task cap still applies to the anonymous bucket.
/// </para>
/// </remarks>
public sealed class SpecialistA2AServer : A2AServer
{
    private readonly ActiveTaskLimiter _limiter;
    private readonly ILogger _log;

    public SpecialistA2AServer(
        IAgentHandler handler,
        ITaskStore store,
        ChannelEventNotifier notifier,
        ILogger<A2AServer> logger,
        A2AServerOptions options,
        ActiveTaskLimiter limiter)
        : base(handler, store, notifier, logger, options)
    {
        _limiter = limiter;
        _log = logger;
    }

    private void Admit()
    {
        if (_limiter.Full(AmbientContext.CurrentUserEmail))
        {
            _log.LogWarning("a2a.too_many_active_tasks total={Total}", _limiter.Total);
            throw new A2AException(A2AWire.TooManyTasksText, A2AErrorCode.InternalError);
        }
    }

    /// <summary>True for a caller with no resolved user identity (empty email, or the middleware's <c>system</c>).</summary>
    internal static bool Anonymous => AmbientContext.CurrentUserEmail is "" or "system";

    private static A2AException TaskNotFound() => new("Task not found", A2AErrorCode.TaskNotFound);

    private A2AException Masked(string method, Exception ex)
    {
        _log.LogError(ex, "a2a.handler_error method={Method} detail={Detail}", method, LogSafe.Clean(ex.GetType().Name));
        return new A2AException(A2AWire.InternalErrorText, A2AErrorCode.InternalError);
    }

    public override async Task<SendMessageResponse> SendMessageAsync(
        SendMessageRequest request, A2ARequestContext? requestContext, CancellationToken cancellationToken = default)
    {
        Admit();
        if (Anonymous && !string.IsNullOrEmpty(request.Message?.TaskId))
        {
            throw TaskNotFound(); // continuing a stored task by id is a read of it
        }

        try
        {
            return await base.SendMessageAsync(request, requestContext, cancellationToken);
        }
        catch (Exception ex) when (ex is not A2AException and not OperationCanceledException)
        {
            throw Masked("SendMessage", ex);
        }
    }

    public override async IAsyncEnumerable<StreamResponse> SendStreamingMessageAsync(
        SendMessageRequest request,
        A2ARequestContext? requestContext,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        Admit();
        if (Anonymous && !string.IsNullOrEmpty(request.Message?.TaskId))
        {
            throw TaskNotFound();
        }

        await foreach (var item in Guarded("SendStreamingMessage", base.SendStreamingMessageAsync(request, requestContext, cancellationToken), cancellationToken))
        {
            yield return item;
        }
    }

    public override async IAsyncEnumerable<StreamResponse> SubscribeToTaskAsync(
        SubscribeToTaskRequest request,
        A2ARequestContext? requestContext,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        if (Anonymous)
        {
            throw TaskNotFound();
        }

        await foreach (var item in Guarded("SubscribeToTask", base.SubscribeToTaskAsync(request, requestContext, cancellationToken), cancellationToken))
        {
            yield return item;
        }
    }

    public override async Task<AgentTask> GetTaskAsync(
        GetTaskRequest request, A2ARequestContext? requestContext, CancellationToken cancellationToken = default)
    {
        if (Anonymous)
        {
            throw TaskNotFound();
        }

        try
        {
            return await base.GetTaskAsync(request, requestContext, cancellationToken);
        }
        catch (Exception ex) when (ex is not A2AException and not OperationCanceledException)
        {
            throw Masked("GetTask", ex);
        }
    }

    public override async Task<ListTasksResponse> ListTasksAsync(
        ListTasksRequest request, A2ARequestContext? requestContext, CancellationToken cancellationToken = default)
    {
        if (Anonymous)
        {
            // The shared anonymous owner would list every anonymous caller's tasks.
            return new ListTasksResponse { Tasks = [], NextPageToken = string.Empty, PageSize = 0, TotalSize = 0 };
        }

        try
        {
            return await base.ListTasksAsync(request, requestContext, cancellationToken);
        }
        catch (Exception ex) when (ex is not A2AException and not OperationCanceledException)
        {
            throw Masked("ListTasks", ex);
        }
    }

    public override async Task<AgentTask> CancelTaskAsync(
        CancelTaskRequest request, A2ARequestContext? requestContext, CancellationToken cancellationToken = default)
    {
        try
        {
            return await base.CancelTaskAsync(request, requestContext, cancellationToken);
        }
        catch (Exception ex) when (ex is not A2AException and not OperationCanceledException)
        {
            throw Masked("CancelTask", ex);
        }
    }

    /// <summary>
    /// Re-yields <paramref name="source"/>, turning an unexpected exception into the fixed error. A
    /// <c>yield</c> may not sit inside a <c>try</c> with a <c>catch</c>, so the enumerator is driven by hand.
    /// </summary>
    private async IAsyncEnumerable<StreamResponse> Guarded(
        string method,
        IAsyncEnumerable<StreamResponse> source,
        [EnumeratorCancellation] CancellationToken cancellationToken)
    {
        await using var enumerator = source.GetAsyncEnumerator(cancellationToken);
        while (true)
        {
            try
            {
                if (!await enumerator.MoveNextAsync())
                {
                    yield break;
                }
            }
            catch (Exception ex) when (ex is not A2AException and not OperationCanceledException)
            {
                throw Masked(method, ex);
            }

            yield return enumerator.Current;
        }
    }
}
