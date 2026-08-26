// MAF v1 — Chapter 31: Retry and Compensation (Saga Pattern) (.NET)
//
// No LLM — the saga pattern is plain orchestration logic, not agent
// reasoning. Three in-memory "services" (stock, payment, shipment) stand in
// for independent API/DB calls that a single database transaction could never
// span. Each step gets an explicit compensating action that undoes it if a
// later step fails, so a partially-completed order unwinds cleanly instead of
// leaving orphaned state.
//
// The distinction that carries the whole chapter is transient vs genuine
// failure. A TransientException is worth retrying — a flaky network hop that
// will probably succeed next attempt. Anything else must compensate
// immediately: retrying a declined card wastes time and, if the call is not
// idempotent, can double-charge the customer.
//
// Run:
//   cd tutorials/31-retry-and-compensation/dotnet
//   dotnet run

namespace MafV1.Ch31.Saga;

// ─────────────── Errors ───────────────

/// <summary>A retryable, temporary failure — e.g. a network timeout.</summary>
public sealed class TransientException(string message) : Exception(message);

/// <summary>A genuine failure. Retrying will not add inventory that does not exist.</summary>
public sealed class OutOfStockException(string message) : Exception(message);

/// <summary>A genuine failure. Retrying will not turn a decline into an approval.</summary>
public sealed class PaymentDeclinedException(string message) : Exception(message);

// ─────────────── In-memory backends ───────────────

/// <summary>
/// Toy stand-ins for three independent services: inventory, payments, and
/// shipping. A real saga calls three separate APIs or databases here — none of
/// which share a transaction with the others. That absence is the reason
/// compensation exists at all.
/// </summary>
public sealed class Backends
{
    private readonly int _reserveFlakyCalls;
    private int _reserveAttempts;

    /// <param name="reserveStockFlakyCalls">
    /// Simulates a flaky inventory service: the first N calls throw
    /// <see cref="TransientException"/>, then it behaves normally. Lets the
    /// demo show a retry that actually succeeds rather than one that gives up.
    /// </param>
    public Backends(int reserveStockFlakyCalls = 0) => _reserveFlakyCalls = reserveStockFlakyCalls;

    public Dictionary<string, int> Stock { get; } = new() { ["widget"] = 10, ["gadget"] = 0 };
    public Dictionary<string, int> Reservations { get; } = new();
    public Dictionary<string, decimal> Payments { get; } = new();
    public Dictionary<string, string> Shipments { get; } = new();

    /// <summary>How many times reserve_stock has been attempted, retries included.</summary>
    public int ReserveAttempts => _reserveAttempts;

    // ─────────────── Step actions ───────────────

    public void ReserveStock(string productId, int quantity)
    {
        _reserveAttempts++;
        if (_reserveAttempts <= _reserveFlakyCalls)
        {
            throw new TransientException($"inventory service timed out (attempt {_reserveAttempts})");
        }

        int available = Stock.GetValueOrDefault(productId);
        if (available < quantity)
        {
            throw new OutOfStockException($"only {available} '{productId}' in stock, need {quantity}");
        }

        Stock[productId] = available - quantity;
        Reservations[productId] = Reservations.GetValueOrDefault(productId) + quantity;
    }

    public void ChargePayment(string orderId, decimal amount, bool shouldFail = false)
    {
        if (shouldFail)
        {
            throw new PaymentDeclinedException($"payment declined for order {orderId}");
        }

        Payments[orderId] = amount;
    }

    public void CreateShipment(string orderId, bool shouldFail = false)
    {
        if (shouldFail)
        {
            throw new InvalidOperationException($"shipment carrier rejected order {orderId}");
        }

        Shipments[orderId] = "created";
    }

    // ─────────────── Compensating actions ───────────────
    //
    // Each compensation is the exact opposite of its matching action — that is
    // the saga contract. There is no database rollback across three services;
    // this is the only way to undo a partially-completed order.

    public void ReleaseStock(string productId, int quantity)
    {
        Stock[productId] = Stock.GetValueOrDefault(productId) + quantity;
        Reservations[productId] = Reservations.GetValueOrDefault(productId) - quantity;
    }

    public void RefundPayment(string orderId) => Payments.Remove(orderId);

    /// <remarks>
    /// Marks rather than deletes, deliberately. A cancelled shipment is a fact
    /// the carrier already knows about; pretending it never existed would lose
    /// the audit trail that a saga's whole appeal is having.
    /// </remarks>
    public void CancelShipment(string orderId) => Shipments[orderId] = "cancelled";
}

// ─────────────── Saga engine ───────────────

/// <summary>One step and the action that undoes it.</summary>
/// <param name="Name">Used in logs and in <see cref="SagaResult"/>.</param>
/// <param name="Action">The forward operation.</param>
/// <param name="Compensation">The exact inverse of <paramref name="Action"/>.</param>
/// <param name="Retryable">Whether a <see cref="TransientException"/> is worth another attempt.</param>
public sealed record SagaStep(
    string Name,
    Action Action,
    Action Compensation,
    bool Retryable = false);

/// <summary>What a saga run did, including what it had to undo.</summary>
public sealed record SagaResult(
    string OrderId,
    bool Succeeded,
    IReadOnlyList<string> CompletedSteps,
    string? FailedStep = null,
    IReadOnlyList<string>? CompensatedSteps = null)
{
    public IReadOnlyList<string> Compensated => CompensatedSteps ?? Array.Empty<string>();

    public override string ToString() =>
        $"SagaResult(order={OrderId}, succeeded={Succeeded}, completed=[{string.Join(", ", CompletedSteps)}]"
        + $", failed={FailedStep ?? "-"}, compensated=[{string.Join(", ", Compensated)}])";
}

public static class SagaRunner
{
    /// <summary>
    /// Runs steps in order, compensating backwards on failure.
    /// </summary>
    /// <param name="maxAttempts">Attempts per retryable step, including the first.</param>
    /// <param name="baseDelay">
    /// First backoff interval; doubles per attempt. Zero in tests — the
    /// backoff schedule is worth demonstrating and not worth waiting for.
    /// </param>
    /// <param name="log">Where progress goes. Console in the app, a list in tests.</param>
    public static async Task<SagaResult> RunAsync(
        string orderId,
        IReadOnlyList<SagaStep> steps,
        int maxAttempts = 3,
        TimeSpan baseDelay = default,
        Action<string>? log = null)
    {
        log ??= _ => { };
        var completed = new List<SagaStep>();

        foreach (SagaStep step in steps)
        {
            int attempt = 0;
            while (true)
            {
                attempt++;
                try
                {
                    step.Action();
                }
                catch (TransientException ex) when (step.Retryable && attempt < maxAttempts)
                {
                    // Exponential backoff. The `when` clause matters: a
                    // TransientException on a non-retryable step, or after the
                    // budget is spent, falls through to the handlers below and
                    // compensates rather than looping.
                    TimeSpan delay = baseDelay * Math.Pow(2, attempt - 1);
                    log($"  [retry] {step.Name}: {ex.Message} "
                        + $"(attempt {attempt}/{maxAttempts}, backing off {delay.TotalSeconds:F2}s)");
                    if (delay > TimeSpan.Zero)
                    {
                        await Task.Delay(delay).ConfigureAwait(false);
                    }

                    continue;
                }
                catch (TransientException ex)
                {
                    log($"  [failed] {step.Name}: {ex.Message} (retries exhausted)");
                    return Unwind(orderId, completed, step, log);
                }
                catch (Exception ex)
                {
                    log($"  [failed] {step.Name}: {ex.Message} (not retryable — compensating immediately)");
                    return Unwind(orderId, completed, step, log);
                }

                log($"  [ok] {step.Name}");
                completed.Add(step);
                break;
            }
        }

        log($"  [done] order {orderId} placed successfully");
        return new SagaResult(orderId, true, completed.Select(s => s.Name).ToList());
    }

    /// <summary>
    /// Walks backward through completed steps, undoing each — the unwind that
    /// makes the pattern work. Reverse order is not cosmetic: a later step may
    /// depend on an earlier one, so undoing forwards can fail halfway and leave
    /// worse state than doing nothing.
    /// </summary>
    private static SagaResult Unwind(
        string orderId,
        List<SagaStep> completed,
        SagaStep failedStep,
        Action<string> log)
    {
        var compensated = new List<string>();
        foreach (SagaStep step in Enumerable.Reverse(completed))
        {
            log($"  [compensate] undoing {step.Name}");
            step.Compensation();
            compensated.Add(step.Name);
        }

        return new SagaResult(
            orderId,
            false,
            completed.Select(s => s.Name).ToList(),
            failedStep.Name,
            compensated);
    }
}

// ─────────────── The "place an order" saga ───────────────

public static class Program
{
    public static IReadOnlyList<SagaStep> BuildPlaceOrderSaga(
        Backends backends,
        string orderId,
        string productId,
        int quantity,
        decimal amount,
        bool failPayment = false,
        bool failShipment = false) =>
        new[]
        {
            new SagaStep(
                Name: "reserve_stock",
                Action: () => backends.ReserveStock(productId, quantity),
                Compensation: () => backends.ReleaseStock(productId, quantity),
                Retryable: true),
            new SagaStep(
                Name: "charge_payment",
                Action: () => backends.ChargePayment(orderId, amount, failPayment),
                Compensation: () => backends.RefundPayment(orderId)),
            new SagaStep(
                Name: "create_shipment",
                Action: () => backends.CreateShipment(orderId, failShipment),
                Compensation: () => backends.CancelShipment(orderId)),
        };

    public static async Task<int> Main()
    {
        Console.WriteLine("=== Scenario 1: happy path — all three steps succeed ===");
        var backends = new Backends();
        SagaResult result = await SagaRunner.RunAsync(
            "order-1",
            BuildPlaceOrderSaga(backends, "order-1", "widget", 2, 49.99m),
            log: Console.WriteLine);
        Console.WriteLine(result);

        Console.WriteLine();
        Console.WriteLine("=== Scenario 2: transient blip on reserve_stock, retried, then succeeds ===");
        backends = new Backends(reserveStockFlakyCalls: 2);
        result = await SagaRunner.RunAsync(
            "order-2",
            BuildPlaceOrderSaga(backends, "order-2", "widget", 1, 19.99m),
            baseDelay: TimeSpan.FromMilliseconds(10),
            log: Console.WriteLine);
        Console.WriteLine(result);

        Console.WriteLine();
        Console.WriteLine("=== Scenario 3: payment declined — genuine failure, unwind reserved stock ===");
        backends = new Backends();
        result = await SagaRunner.RunAsync(
            "order-3",
            BuildPlaceOrderSaga(backends, "order-3", "widget", 3, 99.99m, failPayment: true),
            log: Console.WriteLine);
        Console.WriteLine(result);

        return 0;
    }
}
