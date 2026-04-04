export default function ChatLoading() {
  return (
    <div className="flex h-full">
      {/* Conversation list skeleton (desktop) */}
      <aside className="hidden w-60 shrink-0 flex-col border-r bg-muted/30 lg:flex">
        <div className="flex items-center justify-between px-3 py-2.5">
          <div className="h-4 w-24 animate-pulse rounded bg-muted" />
          <div className="size-6 animate-pulse rounded bg-muted" />
        </div>
        <div className="border-t" />
        <div className="flex flex-col gap-1.5 p-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <div
              key={i}
              className="h-7 animate-pulse rounded-md bg-muted"
              style={{ width: `${65 + Math.random() * 30}%` }}
            />
          ))}
        </div>
      </aside>

      {/* Main area skeleton */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <div className="flex h-11 items-center border-b px-4">
          <div className="h-4 w-28 animate-pulse rounded bg-muted" />
        </div>

        {/* Messages skeleton */}
        <div className="flex-1 px-4 py-6">
          <div className="mx-auto flex max-w-3xl flex-col gap-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className={`flex gap-2.5 ${i % 2 === 0 ? "justify-start" : "justify-end"}`}
              >
                <div className="size-7 shrink-0 animate-pulse rounded-full bg-muted" />
                <div
                  className="h-12 animate-pulse rounded-2xl bg-muted"
                  style={{ width: `${35 + Math.random() * 35}%` }}
                />
              </div>
            ))}
          </div>
        </div>

        {/* Input skeleton */}
        <div className="border-t px-4 py-3">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <div className="h-10 flex-1 animate-pulse rounded-lg bg-muted" />
            <div className="size-8 shrink-0 animate-pulse rounded-lg bg-muted" />
          </div>
        </div>
      </div>
    </div>
  );
}
