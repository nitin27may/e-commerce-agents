"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ShieldAlert } from "lucide-react";
import { useAuth } from "@/lib/auth-context";

/**
 * Renders `children` for staff (support and admin) only.
 *
 * Pages that expose how the system works inside (agent runs, the agent catalog with its tools) are for the people
 * who operate the store. Customers and sellers get answers, not machinery, so those pages are closed to them as well
 * as hidden from their navigation. The API enforces its own access rules; this is the page-level counterpart.
 */
export function StaffOnly({ children }: { children: ReactNode }) {
  const { user, isStaff, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !user) router.replace("/login");
  }, [isLoading, user, router]);

  if (isLoading || !user) return null;
  if (!isStaff) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="text-center">
          <div className="mx-auto flex size-16 items-center justify-center rounded-full bg-destructive/10">
            <ShieldAlert className="size-8 text-destructive" />
          </div>
          <h2 className="mt-4 text-lg font-semibold">Access Denied</h2>
          <p className="mt-1 text-sm text-muted-foreground">Staff access required (support or admin).</p>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
