import { ShieldAlert } from "lucide-react";

/**
 * Role gate message for seller and admin pages. Mirrors the StaffOnly gate so a
 * blocked page looks the same whichever role check stopped it.
 */
export function AccessDenied({ description }: { description: string }) {
  return (
    <div className="flex min-h-[60vh] items-center justify-center px-4">
      <div className="text-center">
        <div className="mx-auto flex size-16 items-center justify-center rounded-full bg-destructive/10">
          <ShieldAlert className="size-8 text-destructive" aria-hidden />
        </div>
        <h2 className="mt-4 text-lg font-semibold">Access Denied</h2>
        <p className="mt-1 text-sm text-muted-foreground">{description}</p>
      </div>
    </div>
  );
}
