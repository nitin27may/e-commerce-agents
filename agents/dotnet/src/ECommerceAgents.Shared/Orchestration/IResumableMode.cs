namespace ECommerceAgents.Shared.Orchestration;

/// <summary>
/// A mode whose run can stop on a human and be picked up later.
/// </summary>
/// <remarks>
/// Kept off <see cref="IOrchestrationMode"/> on purpose: three of the four registered
/// modes never pause, and giving them a Resume they must throw from would be a worse
/// contract than not having one. The resume route tests for this interface instead, so
/// <see cref="ModeRegistry"/> stays the single lookup point — resolving the concrete mode
/// from DI would need a second registration and two lifetimes for one object.
///
/// Python reaches the equivalent method by importing the class directly in its route
/// (<c>routes/orchestration.py:210</c>); its docstring notes the same thing, that resume
/// is deliberately not part of the mode protocol.
/// </remarks>
public interface IResumableMode
{
    /// <param name="sessionId">Keys the checkpoints the paused run wrote.</param>
    /// <param name="checkpointId">The checkpoint to restore from.</param>
    /// <param name="requestId">MAF's resume token, checked against what the restored run asks for.</param>
    /// <param name="approved">The human's decision.</param>
    Task<ModeRunResult> ResumeAsync(
        string sessionId,
        string checkpointId,
        string requestId,
        bool approved,
        CancellationToken ct = default
    );
}
