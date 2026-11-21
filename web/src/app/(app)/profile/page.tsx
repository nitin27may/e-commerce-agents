"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { api, type UserProfile as Profile } from "@/lib/api";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { CHIP_CLASS, TONE_BADGE_CLASS } from "@/components/ui/status-badge";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";
import {
  Award,
  DollarSign,
  Package,
  MessageSquare,
  Shield,
  Crown,
  Mail,
  User,
  Loader2,
  Check,
  Brain,
  Trash2,
  Star,
  X,
  TrendingUp,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TIER_COLORS: Record<string, { bg: string; text: string; accent: string }> = {
  bronze: { bg: "bg-warning/10", text: "text-[color-mix(in_oklab,var(--warning)_45%,black)] dark:text-warning", accent: "#CD7F32" },
  silver: { bg: "bg-muted", text: "text-muted-foreground", accent: "#C0C0C0" },
  gold: { bg: "bg-warning/15", text: "text-[color-mix(in_oklab,var(--warning)_45%,black)] dark:text-warning", accent: "#FFD700" },
};

const ROLE_COLORS: Record<string, string> = {
  customer: CHIP_CLASS.primary,
  power_user: CHIP_CLASS.teal,
  support: TONE_BADGE_CLASS.warning,
  seller: TONE_BADGE_CLASS.success,
  admin: TONE_BADGE_CLASS.destructive,
};

const TIER_THRESHOLDS: Record<string, { next: string; amount: number } | null> = {
  bronze: { next: "Silver", amount: 1000 },
  silver: { next: "Gold", amount: 3000 },
  gold: null,
};

function formatCurrency(amount: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  }).format(amount);
}

function formatDate(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString("en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
}

function getInitials(name: string): string {
  return name
    .split(" ")
    .map((part) => part[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);
}

function formatRole(role: string): string {
  return role
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ProfilePage() {
  const router = useRouter();
  const { user, isLoading: authLoading } = useAuth();

  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [memories, setMemories] = useState<
    { id: string; category: string; content: string; importance: number; created_at: string }[]
  >([]);
  const [memoriesLoading, setMemoriesLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
    }
  }, [user, authLoading, router]);

  const loadProfile = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getProfile();
      setProfile(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load profile");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMemories = useCallback(async () => {
    setMemoriesLoading(true);
    try {
      const rows = await api.getUserMemories();
      setMemories(rows ?? []);
    } catch {
      // non-fatal — memories section just stays empty
    } finally {
      setMemoriesLoading(false);
    }
  }, []);

  useLoadEffect(loadProfile, Boolean(user));
  useLoadEffect(loadMemories, Boolean(user));

  async function handleDeleteMemory(id: string) {
    setDeletingId(id);
    try {
      await api.deleteUserMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
    } finally {
      setDeletingId(null);
    }
  }

  if (authLoading) return null;

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="border-b border-border bg-card">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
              <User className="size-5 text-primary-foreground" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-foreground">My Profile</h1>
              <p className="text-sm text-muted-foreground">
                Your account details and loyalty status
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-6 animate-spin text-primary" />
            <span className="ml-2 text-sm text-muted-foreground">
              Loading profile...
            </span>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {!loading && !error && profile && (
          <div className="space-y-8">
            {/* Profile header card */}
            <Card>
              <CardContent className="pt-6">
                <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
                  {/* Avatar */}
                  <div
                    className="flex size-20 shrink-0 items-center justify-center rounded-full text-2xl font-bold text-white"
                    style={{ backgroundColor: TIER_COLORS[profile.loyalty_tier]?.accent ?? "#64748b" }}
                  >
                    {getInitials(profile.name)}
                  </div>

                  <div className="flex-1 text-center sm:text-left">
                    <div className="flex flex-col items-center gap-2 sm:flex-row">
                      <h2 className="text-xl font-bold text-foreground">
                        {profile.name}
                      </h2>
                      <Badge
                        className={`${ROLE_COLORS[profile.role] ?? "bg-muted text-muted-foreground"} border-0`}
                      >
                        {profile.role === "admin" && (
                          <Shield className="mr-1 size-3" />
                        )}
                        {formatRole(profile.role)}
                      </Badge>
                    </div>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {profile.email}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Member since {formatDate(profile.member_since)}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Stats cards */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {/* Loyalty Tier */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Loyalty Tier
                  </CardTitle>
                  <Crown
                    className="size-4"
                    style={{ color: TIER_COLORS[profile.loyalty_tier]?.accent ?? "#64748b" }}
                  />
                </CardHeader>
                <CardContent>
                  <div
                    className="text-2xl font-bold capitalize"
                    style={{ color: TIER_COLORS[profile.loyalty_tier]?.accent ?? "#64748b" }}
                  >
                    {profile.loyalty_tier}
                  </div>
                </CardContent>
              </Card>

              {/* Total Spend */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Total Spend
                  </CardTitle>
                  <DollarSign className="size-4 text-success" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-foreground">
                    {formatCurrency(profile.total_spend)}
                  </div>
                </CardContent>
              </Card>

              {/* Orders */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Orders
                  </CardTitle>
                  <Package className="size-4 text-info" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-foreground">
                    {profile.order_count}
                  </div>
                </CardContent>
              </Card>

              {/* Reviews */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Reviews
                  </CardTitle>
                  <MessageSquare className="size-4 text-warning" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-foreground">
                    {profile.review_count}
                  </div>
                </CardContent>
              </Card>
            </div>

            {/* Loyalty Benefits */}
            <Card>
              <CardHeader>
                <div className="flex items-center gap-2">
                  <Award
                    className="size-5"
                    style={{ color: TIER_COLORS[profile.loyalty_tier]?.accent ?? "#64748b" }}
                  />
                  <CardTitle className="text-base font-semibold text-foreground">
                    Loyalty Benefits
                  </CardTitle>
                  <Badge
                    className={`${TIER_COLORS[profile.loyalty_tier]?.bg ?? "bg-muted"} ${TIER_COLORS[profile.loyalty_tier]?.text ?? "text-muted-foreground"} border-0 capitalize`}
                  >
                    {profile.loyalty_tier} Tier
                  </Badge>
                </div>
              </CardHeader>
              <Separator />
              <CardContent className="pt-6">
                <div className="space-y-4">
                  {/* Discount */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <DollarSign className="size-4 text-muted-foreground" />
                      <span>Order Discount</span>
                    </div>
                    <span className="text-sm font-medium text-foreground">
                      {profile.tier_benefits.discount_pct > 0
                        ? `${profile.tier_benefits.discount_pct}% on all orders`
                        : "Not available"}
                    </span>
                  </div>

                  {/* Free Shipping */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Package className="size-4 text-muted-foreground" />
                      <span>Free Shipping</span>
                    </div>
                    <span className="text-sm font-medium text-foreground">
                      {profile.tier_benefits.free_shipping_threshold != null
                        ? `Free shipping on orders over ${formatCurrency(profile.tier_benefits.free_shipping_threshold)}`
                        : "Not available"}
                    </span>
                  </div>

                  {/* Priority Support */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Shield className="size-4 text-muted-foreground" />
                      <span>Priority Support</span>
                    </div>
                    <span className="flex items-center gap-1 text-sm font-medium">
                      {profile.tier_benefits.priority_support ? (
                        <>
                          <Check className="size-4 text-success" />
                          <span className="text-[color-mix(in_oklab,var(--success)_62%,black)] dark:text-success">Included</span>
                        </>
                      ) : (
                        <>
                          <X className="size-4 text-muted-foreground" />
                          <span className="text-muted-foreground">Not available</span>
                        </>
                      )}
                    </span>
                  </div>

                  {/* Tier progress */}
                  {TIER_THRESHOLDS[profile.loyalty_tier] && (
                    <>
                      <Separator />
                      <div className="flex items-start gap-2">
                        <TrendingUp className="mt-0.5 size-4 text-primary" />
                        <div className="flex-1">
                          <p className="text-sm font-medium text-muted-foreground">
                            Progress to {TIER_THRESHOLDS[profile.loyalty_tier]!.next}
                          </p>
                          {(() => {
                            const threshold = TIER_THRESHOLDS[profile.loyalty_tier]!.amount;
                            const remaining = Math.max(0, threshold - profile.total_spend);
                            const progress = Math.min(100, (profile.total_spend / threshold) * 100);
                            return (
                              <>
                                <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted">
                                  <div
                                    className="h-full rounded-full bg-primary transition-all"
                                    style={{ width: `${progress}%` }}
                                  />
                                </div>
                                <p className="mt-1 text-xs text-muted-foreground">
                                  {remaining > 0
                                    ? `Spend ${formatCurrency(remaining)} more to reach ${TIER_THRESHOLDS[profile.loyalty_tier]!.next}`
                                    : `You've qualified for ${TIER_THRESHOLDS[profile.loyalty_tier]!.next}!`}
                                </p>
                              </>
                            );
                          })()}
                        </div>
                      </div>
                    </>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* Account Details */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base font-semibold text-foreground">
                  Account Details
                </CardTitle>
              </CardHeader>
              <Separator />
              <CardContent className="pt-6">
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Mail className="size-4 text-muted-foreground" />
                      <span>Email</span>
                    </div>
                    <span className="text-sm font-medium text-foreground">
                      {profile.email}
                    </span>
                  </div>

                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Shield className="size-4 text-muted-foreground" />
                      <span>Role</span>
                    </div>
                    <Badge
                      className={`${ROLE_COLORS[profile.role] ?? "bg-muted text-muted-foreground"} border-0`}
                    >
                      {formatRole(profile.role)}
                    </Badge>
                  </div>

                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <User className="size-4 text-muted-foreground" />
                      <span>User ID</span>
                    </div>
                    <span className="font-mono text-xs text-muted-foreground">
                      {profile.id.slice(0, 8)}...{profile.id.slice(-4)}
                    </span>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* AI Memory */}
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Brain className="size-5 text-primary" />
                    <CardTitle className="text-base font-semibold text-foreground">
                      AI Memory
                    </CardTitle>
                  </div>
                  <span className="text-xs text-muted-foreground">
                    What the agents remember about you
                  </span>
                </div>
              </CardHeader>
              <Separator />
              <CardContent className="pt-6">
                {memoriesLoading ? (
                  <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" />
                    <span>Loading memories...</span>
                  </div>
                ) : memories.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-border py-8 text-center">
                    <Brain className="mx-auto mb-2 size-8 text-muted-foreground/40" />
                    <p className="text-sm text-muted-foreground">
                      No memories yet. Chat with the product or review agents to build your profile.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {memories.map((memory) => (
                      <div
                        key={memory.id}
                        className="flex items-start justify-between gap-3 rounded-lg border border-border bg-muted/30 px-4 py-3"
                      >
                        <div className="flex-1 space-y-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <Badge
                              className="border-0 bg-primary/10 text-primary text-xs capitalize"
                            >
                              {memory.category}
                            </Badge>
                            <span className="flex items-center gap-0.5" title={`Importance: ${memory.importance}/10`}>
                              {Array.from({ length: Math.min(5, Math.ceil(memory.importance / 2)) }).map((_, i) => (
                                <Star key={i} className="size-3 fill-warning text-warning" />
                              ))}
                            </span>
                          </div>
                          <p className="text-sm text-foreground">{memory.content}</p>
                          <p className="text-xs text-muted-foreground">
                            {formatDate(memory.created_at)}
                          </p>
                        </div>
                        <button
                          onClick={() => handleDeleteMemory(memory.id)}
                          disabled={deletingId === memory.id}
                          className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                          title="Forget this memory"
                        >
                          {deletingId === memory.id ? (
                            <Loader2 className="size-4 animate-spin" />
                          ) : (
                            <Trash2 className="size-4" />
                          )}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
