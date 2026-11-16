namespace ECommerceAgents.Shared.A2A.Protocol;

/// <summary>
/// Counts the A2A tasks whose executor is running, in total and per caller (the forwarded user's
/// email; anonymous callers share one bucket). Twin of Python's <c>ActiveTaskLimiter</c>.
/// </summary>
/// <remarks>
/// The SDK owns a task independently of the connection that started it, so a client that drops, times
/// out or is slow leaves the run going; without a bound a burst of them is a burst of concurrent model
/// calls and tool runs. Admission is decided twice: <see cref="Full"/> is the cheap pre-check that lets
/// the server answer with a spec error before any task exists, and <see cref="TryAcquire"/> (atomic under
/// a lock) is the real gate, taken by the executor and released in its <c>finally</c>. Nothing queues.
/// </remarks>
public sealed class ActiveTaskLimiter(int maxTotal, int maxPerCaller)
{
    private readonly object _gate = new();
    private readonly Dictionary<string, int> _byCaller = new(StringComparer.Ordinal);
    private int _total;

    public int Total
    {
        get
        {
            lock (_gate)
            {
                return _total;
            }
        }
    }

    public bool Full(string caller)
    {
        lock (_gate)
        {
            return IsFull(caller);
        }
    }

    public bool TryAcquire(string caller)
    {
        lock (_gate)
        {
            if (IsFull(caller))
            {
                return false;
            }

            _byCaller[caller] = _byCaller.GetValueOrDefault(caller) + 1;
            _total++;
            return true;
        }
    }

    public void Release(string caller)
    {
        lock (_gate)
        {
            var held = _byCaller.GetValueOrDefault(caller);
            if (held <= 0)
            {
                return;
            }

            if (held == 1)
            {
                _byCaller.Remove(caller);
            }
            else
            {
                _byCaller[caller] = held - 1;
            }

            _total--;
        }
    }

    private bool IsFull(string caller) =>
        _total >= maxTotal || _byCaller.GetValueOrDefault(caller) >= maxPerCaller;
}
