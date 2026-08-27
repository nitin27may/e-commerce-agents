namespace ECommerceAgents.Shared.Context;

/// <summary>
/// One SSE frame produced from inside a streaming turn, on its way to the browser.
/// </summary>
/// <remarks>
/// <para>
/// <see cref="RequestContext.CurrentStreamWriter"/> used to be a
/// <c>ChannelWriter&lt;string&gt;</c>, and everything written to it was emitted as
/// <c>event: delta</c>. That was enough while a specialist's live text was the only
/// thing worth forwarding mid-turn. It stopped being enough once tool steps needed
/// to reach the browser as they happened rather than in a batch after the answer
/// had finished writing — a channel of bare strings cannot express two kinds of
/// frame, and overloading the delta channel with JSON would have put raw payloads
/// into the chat bubble.
/// </para>
/// <para>
/// The twin of the <c>("delta", ...)</c> / <c>("frame", ...)</c> tuples Python puts
/// on <c>current_stream_queue</c> (<c>orchestrator/routes/chat.py</c>).
/// </para>
/// </remarks>
/// <param name="Event">SSE event name — <c>delta</c>, <c>step</c>, …</param>
/// <param name="Data">Already-serialised payload for the frame's <c>data:</c> line.</param>
public readonly record struct StreamFrame(string Event, string Data);
