"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog";
import {
  CheckCircle2,
  XCircle,
  Loader2,
  ShieldAlert,
  ClipboardList,
  MessageSquare,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AccessRequest {
  id: string;
  user_email: string;
  agent_id: string;
  agent_name: string;
  role_requested: string;
  use_case: string;
  status: "pending" | "approved" | "denied";
  created_at: string;
  reviewed_by?: string;
  admin_notes?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return dateStr;
  }
}

function statusBadge(status: string) {
  switch (status) {
    case "approved":
      return (
        <Badge
          variant="outline"
          className="border-emerald-200 bg-emerald-50 text-emerald-700"
        >
          Approved
        </Badge>
      );
    case "denied":
      return (
        <Badge
          variant="outline"
          className="border-red-200 bg-red-50 text-red-700"
        >
          Denied
        </Badge>
      );
    default:
      return (
        <Badge
          variant="outline"
          className="border-amber-200 bg-amber-50 text-amber-700"
        >
          Pending
        </Badge>
      );
  }
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminRequestsPage() {
  const router = useRouter();
  const { user, isAdmin, isLoading: authLoading } = useAuth();

  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Action dialog state
  const [actionTarget, setActionTarget] = useState<{
    request: AccessRequest;
    action: "approve" | "deny";
  } | null>(null);
  const [adminNotes, setAdminNotes] = useState("");
  const [actionLoading, setActionLoading] = useState(false);

  // Feedback message
  const [feedback, setFeedback] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
    }
  }, [user, authLoading, router]);

  const loadRequests = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getAccessRequests();
      setRequests(data);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load requests"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user && isAdmin) loadRequests();
  }, [user, isAdmin, loadRequests]);

  async function handleAction() {
    if (!actionTarget) return;

    setActionLoading(true);
    setFeedback(null);
    try {
      if (actionTarget.action === "approve") {
        await api.approveRequest(actionTarget.request.id, adminNotes || undefined);
      } else {
        await api.denyRequest(actionTarget.request.id, adminNotes || undefined);
      }
      setFeedback({
        type: "success",
        message: `Request ${actionTarget.action === "approve" ? "approved" : "denied"} successfully.`,
      });
      setActionTarget(null);
      setAdminNotes("");
      await loadRequests();
    } catch (err) {
      setFeedback({
        type: "error",
        message: err instanceof Error ? err.message : "Action failed",
      });
    } finally {
      setActionLoading(false);
    }
  }

  if (authLoading) return null;

  if (!isAdmin) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50">
        <div className="text-center">
          <div className="mx-auto flex size-16 items-center justify-center rounded-full bg-red-50">
            <ShieldAlert className="size-8 text-red-500" />
          </div>
          <h2 className="mt-4 text-lg font-semibold text-slate-900">
            Access Denied
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            You do not have admin privileges to view this page.
          </p>
        </div>
      </div>
    );
  }

  const pendingRequests = requests.filter((r) => r.status === "pending");
  const resolvedRequests = requests.filter((r) => r.status !== "pending");

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-teal-600">
              <ClipboardList className="size-5 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-slate-900">
                Access Requests
              </h1>
              <p className="text-sm text-slate-500">
                Review and manage agent access requests
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {/* Feedback banner */}
        {feedback && (
          <div
            className={`mb-6 flex items-center gap-2 rounded-lg border px-4 py-3 text-sm ${
              feedback.type === "success"
                ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                : "border-red-200 bg-red-50 text-red-700"
            }`}
          >
            {feedback.type === "success" ? (
              <CheckCircle2 className="size-4 shrink-0" />
            ) : (
              <XCircle className="size-4 shrink-0" />
            )}
            {feedback.message}
            <button
              onClick={() => setFeedback(null)}
              className="ml-auto text-xs underline underline-offset-2 opacity-70 hover:opacity-100"
            >
              Dismiss
            </button>
          </div>
        )}

        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-6 animate-spin text-teal-600" />
            <span className="ml-2 text-sm text-slate-500">
              Loading requests...
            </span>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {!loading && !error && (
          <div className="space-y-8">
            {/* Pending requests */}
            <div className="rounded-xl border border-slate-200 bg-white">
              <div className="flex items-center justify-between px-4 py-3">
                <h2 className="text-sm font-semibold text-slate-700">
                  Pending Requests
                </h2>
                <Badge
                  variant="outline"
                  className="border-amber-200 bg-amber-50 text-amber-700"
                >
                  {pendingRequests.length}
                </Badge>
              </div>
              <Separator />
              {pendingRequests.length === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-slate-400">
                  No pending requests.
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>User</TableHead>
                      <TableHead>Agent</TableHead>
                      <TableHead>Role</TableHead>
                      <TableHead className="max-w-[200px]">Use Case</TableHead>
                      <TableHead>Requested</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pendingRequests.map((req) => (
                      <TableRow key={req.id}>
                        <TableCell className="font-medium text-slate-800">
                          {req.user_email}
                        </TableCell>
                        <TableCell>{req.agent_name}</TableCell>
                        <TableCell>
                          <Badge variant="secondary" className="text-xs font-normal">
                            {req.role_requested}
                          </Badge>
                        </TableCell>
                        <TableCell className="max-w-[200px]">
                          <p className="truncate text-slate-500" title={req.use_case}>
                            {req.use_case}
                          </p>
                        </TableCell>
                        <TableCell className="text-slate-500">
                          {formatDate(req.created_at)}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1.5">
                            {/* Approve dialog */}
                            <Dialog
                              onOpenChange={(open) => {
                                if (open) {
                                  setActionTarget({ request: req, action: "approve" });
                                  setAdminNotes("");
                                } else {
                                  setActionTarget(null);
                                }
                              }}
                            >
                              <DialogTrigger
                                render={
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    className="border-emerald-200 text-emerald-700 hover:bg-emerald-50"
                                  />
                                }
                              >
                                <CheckCircle2 className="mr-1 size-3.5" />
                                Approve
                              </DialogTrigger>
                              <DialogContent className="sm:max-w-md">
                                <DialogHeader>
                                  <DialogTitle>Approve Request</DialogTitle>
                                  <DialogDescription>
                                    Grant{" "}
                                    <span className="font-medium text-slate-700">
                                      {req.user_email}
                                    </span>{" "}
                                    access to{" "}
                                    <span className="font-medium text-slate-700">
                                      {req.agent_name}
                                    </span>
                                    .
                                  </DialogDescription>
                                </DialogHeader>
                                <div className="space-y-3 py-2">
                                  <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
                                    <p className="text-xs font-medium text-slate-500">
                                      Use Case
                                    </p>
                                    <p className="mt-0.5 text-sm text-slate-700">
                                      {req.use_case}
                                    </p>
                                  </div>
                                  <div className="space-y-1.5">
                                    <Label htmlFor="admin-notes-approve">
                                      Admin Notes{" "}
                                      <span className="text-slate-400">
                                        (optional)
                                      </span>
                                    </Label>
                                    <Textarea
                                      id="admin-notes-approve"
                                      placeholder="Add any notes..."
                                      value={adminNotes}
                                      onChange={(e) =>
                                        setAdminNotes(e.target.value)
                                      }
                                      className="min-h-16"
                                    />
                                  </div>
                                </div>
                                <DialogFooter>
                                  <DialogClose
                                    render={<Button variant="outline" />}
                                  >
                                    Cancel
                                  </DialogClose>
                                  <Button
                                    onClick={handleAction}
                                    disabled={actionLoading}
                                    className="bg-emerald-600 text-white hover:bg-emerald-700"
                                  >
                                    {actionLoading && (
                                      <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                                    )}
                                    Approve
                                  </Button>
                                </DialogFooter>
                              </DialogContent>
                            </Dialog>

                            {/* Deny dialog */}
                            <Dialog
                              onOpenChange={(open) => {
                                if (open) {
                                  setActionTarget({ request: req, action: "deny" });
                                  setAdminNotes("");
                                } else {
                                  setActionTarget(null);
                                }
                              }}
                            >
                              <DialogTrigger
                                render={
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    className="border-red-200 text-red-600 hover:bg-red-50"
                                  />
                                }
                              >
                                <XCircle className="mr-1 size-3.5" />
                                Deny
                              </DialogTrigger>
                              <DialogContent className="sm:max-w-md">
                                <DialogHeader>
                                  <DialogTitle>Deny Request</DialogTitle>
                                  <DialogDescription>
                                    Deny{" "}
                                    <span className="font-medium text-slate-700">
                                      {req.user_email}
                                    </span>
                                    {" "}access to{" "}
                                    <span className="font-medium text-slate-700">
                                      {req.agent_name}
                                    </span>
                                    .
                                  </DialogDescription>
                                </DialogHeader>
                                <div className="space-y-3 py-2">
                                  <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
                                    <p className="text-xs font-medium text-slate-500">
                                      Use Case
                                    </p>
                                    <p className="mt-0.5 text-sm text-slate-700">
                                      {req.use_case}
                                    </p>
                                  </div>
                                  <div className="space-y-1.5">
                                    <Label htmlFor="admin-notes-deny">
                                      Admin Notes{" "}
                                      <span className="text-slate-400">
                                        (optional)
                                      </span>
                                    </Label>
                                    <Textarea
                                      id="admin-notes-deny"
                                      placeholder="Reason for denial..."
                                      value={adminNotes}
                                      onChange={(e) =>
                                        setAdminNotes(e.target.value)
                                      }
                                      className="min-h-16"
                                    />
                                  </div>
                                </div>
                                <DialogFooter>
                                  <DialogClose
                                    render={<Button variant="outline" />}
                                  >
                                    Cancel
                                  </DialogClose>
                                  <Button
                                    onClick={handleAction}
                                    disabled={actionLoading}
                                    variant="destructive"
                                  >
                                    {actionLoading && (
                                      <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                                    )}
                                    Deny Request
                                  </Button>
                                </DialogFooter>
                              </DialogContent>
                            </Dialog>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </div>

            {/* Resolved requests */}
            {resolvedRequests.length > 0 && (
              <div className="rounded-xl border border-slate-200 bg-white">
                <div className="px-4 py-3">
                  <h2 className="text-sm font-semibold text-slate-700">
                    Resolved Requests
                  </h2>
                </div>
                <Separator />
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>User</TableHead>
                      <TableHead>Agent</TableHead>
                      <TableHead>Role</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Notes</TableHead>
                      <TableHead>Requested</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {resolvedRequests.map((req) => (
                      <TableRow key={req.id}>
                        <TableCell className="font-medium text-slate-800">
                          {req.user_email}
                        </TableCell>
                        <TableCell>{req.agent_name}</TableCell>
                        <TableCell>
                          <Badge variant="secondary" className="text-xs font-normal">
                            {req.role_requested}
                          </Badge>
                        </TableCell>
                        <TableCell>{statusBadge(req.status)}</TableCell>
                        <TableCell className="max-w-[200px]">
                          <p className="truncate text-slate-500" title={req.admin_notes ?? ""}>
                            {req.admin_notes || (
                              <span className="text-slate-300">--</span>
                            )}
                          </p>
                        </TableCell>
                        <TableCell className="text-slate-500">
                          {formatDate(req.created_at)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
