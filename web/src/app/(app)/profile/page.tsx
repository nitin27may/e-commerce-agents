"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { api, type UserProfile as Profile } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CHIP_CLASS, TONE_BADGE_CLASS, TONE_TEXT_CLASS } from "@/components/ui/status-badge";
import { StatCard } from "@/components/ui/stat-card";
import { PageContainer, PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { formatDate, formatPrice } from "@/lib/format";
import { cn } from "@/lib/utils";
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

/** Tier tints come from the warning token (gold, bronze) and muted (silver) so text keeps AA contrast. */
const TIER_STYLE: Record<string, { chip: string; avatar: string }> = {
  bronze: { chip: TONE_BADGE_CLASS.warning, avatar: cn("bg-warning/10", TONE_TEXT_CLASS.warning) },
  silver: { chip: TONE_BADGE_CLASS.neutral, avatar: "bg-muted text-muted-foreground" },
  gold: { chip: cn("bg-warning/15 border-warning/40", TONE_TEXT_CLASS.warning), avatar: cn("bg-warning/15", TONE_TEXT_CLASS.warning) },
};
const DEFAULT_TIER_STYLE = { chip: TONE_BADGE_CLASS.neutral, avatar: "bg-primary/10 text-primary" };

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

const formatCurrency = formatPrice;

function capitalize(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
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
  return capitalize(role.replace(/_/g, " "));
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

  const tier = profile ? (TIER_STYLE[profile.loyalty_tier] ?? DEFAULT_TIER_STYLE) : DEFAULT_TIER_STYLE;
  const nextTier = profile ? TIER_THRESHOLDS[profile.loyalty_tier] : null;

  return (
    <PageContainer>
      <PageHeader icon={User} title="Profile" description="Your account details and loyalty status." />

      {loading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="size-6 animate-spin text-primary" />
          <span className="ml-2 text-sm text-muted-foreground">Loading profile...</span>
        </div>
      )}

      {error && (
        <ErrorState inline message={error} onRetry={loadProfile} homeHref="/home" homeLabel="Back to home" />
      )}

      {!loading && !error && profile && (
        <div className="space-y-6">
          {/* Identity */}
          <Card>
            <CardContent>
              <div className="flex flex-col items-center gap-4 sm:flex-row">
                <div
                  className={cn(
                    "flex size-16 shrink-0 items-center justify-center rounded-full text-xl font-semibold",
                    tier.avatar,
                  )}
                  aria-hidden
                >
                  {getInitials(profile.name)}
                </div>
                <div className="min-w-0 flex-1 text-center sm:text-left">
                  <div className="flex flex-col items-center gap-2 sm:flex-row">
                    <h2 className="text-xl font-semibold tracking-tight text-foreground">{profile.name}</h2>
                    <Badge variant="outline" className={ROLE_COLORS[profile.role] ?? CHIP_CLASS.slate}>
                      {profile.role === "admin" && <Shield className="size-3" />}
                      {formatRole(profile.role)}
                    </Badge>
                    <Badge variant="outline" className={tier.chip}>
                      <Crown className="size-3" />
                      {capitalize(profile.loyalty_tier)} member
                    </Badge>
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">{profile.email}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Member since {formatDate(profile.member_since)}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Stats */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Loyalty tier" value={capitalize(profile.loyalty_tier)} icon={Crown} />
            <StatCard label="Total spend" value={formatCurrency(profile.total_spend)} icon={DollarSign} />
            <StatCard label="Orders" value={profile.order_count} icon={Package} />
            <StatCard label="Reviews" value={profile.review_count} icon={MessageSquare} />
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            {/* Loyalty benefits */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Award className="size-4 text-muted-foreground" aria-hidden />
                  Loyalty benefits
                </CardTitle>
              </CardHeader>
              <CardContent>
                <dl className="space-y-3 text-sm">
                  <div className="flex items-center justify-between gap-4">
                    <dt className="text-muted-foreground">Order discount</dt>
                    <dd className="text-right font-medium text-foreground">
                      {profile.tier_benefits.discount_pct > 0
                        ? `${profile.tier_benefits.discount_pct}% on all orders`
                        : "Not available"}
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-4">
                    <dt className="text-muted-foreground">Free shipping</dt>
                    <dd className="text-right font-medium text-foreground">
                      {profile.tier_benefits.free_shipping_threshold != null
                        ? `On orders over ${formatCurrency(profile.tier_benefits.free_shipping_threshold)}`
                        : "Not available"}
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-4">
                    <dt className="text-muted-foreground">Priority support</dt>
                    <dd className="flex items-center gap-1 font-medium">
                      {profile.tier_benefits.priority_support ? (
                        <span className={cn("flex items-center gap-1", TONE_TEXT_CLASS.success)}>
                          <Check className="size-4" aria-hidden />
                          Included
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 text-muted-foreground">
                          <X className="size-4" aria-hidden />
                          Not available
                        </span>
                      )}
                    </dd>
                  </div>
                </dl>

                {nextTier && (() => {
                  const remaining = Math.max(0, nextTier.amount - profile.total_spend);
                  const progress = Math.min(100, (profile.total_spend / nextTier.amount) * 100);
                  return (
                    <div className="mt-4 border-t border-border pt-4">
                      <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                        <TrendingUp className="size-4 text-muted-foreground" aria-hidden />
                        Progress to {nextTier.next}
                      </div>
                      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted">
                        <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progress}%` }} />
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {remaining > 0
                          ? `Spend ${formatCurrency(remaining)} more to reach ${nextTier.next}.`
                          : `You qualify for ${nextTier.next}.`}
                      </p>
                    </div>
                  );
                })()}
              </CardContent>
            </Card>

            {/* Account details */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <User className="size-4 text-muted-foreground" aria-hidden />
                  Account details
                </CardTitle>
              </CardHeader>
              <CardContent>
                <dl className="space-y-3 text-sm">
                  <div className="flex items-center justify-between gap-4">
                    <dt className="flex items-center gap-2 text-muted-foreground">
                      <Mail className="size-4" aria-hidden />
                      Email
                    </dt>
                    <dd className="truncate font-medium text-foreground">{profile.email}</dd>
                  </div>
                  <div className="flex items-center justify-between gap-4">
                    <dt className="flex items-center gap-2 text-muted-foreground">
                      <Shield className="size-4" aria-hidden />
                      Role
                    </dt>
                    <dd className="font-medium text-foreground">{formatRole(profile.role)}</dd>
                  </div>
                  <div className="flex items-center justify-between gap-4">
                    <dt className="flex items-center gap-2 text-muted-foreground">
                      <User className="size-4" aria-hidden />
                      Account ID
                    </dt>
                    <dd className="font-mono text-xs text-muted-foreground">{profile.id.slice(0, 8)}</dd>
                  </div>
                </dl>
              </CardContent>
            </Card>
          </div>

          {/* Assistant memory */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Brain className="size-4 text-muted-foreground" aria-hidden />
                What the assistant remembers
              </CardTitle>
              <p className="text-sm text-muted-foreground">
                Preferences picked up from your conversations. Remove anything you want forgotten.
              </p>
            </CardHeader>
            <CardContent>
              {memoriesLoading ? (
                <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  <span>Loading...</span>
                </div>
              ) : memories.length === 0 ? (
                <EmptyState
                  icon={Brain}
                  title="Nothing saved yet"
                  description="Chat with the assistant and your preferences will show up here."
                  className="py-10"
                />
              ) : (
                <ul className="space-y-3">
                  {memories.map((memory) => (
                    <li
                      key={memory.id}
                      className="flex items-start justify-between gap-3 rounded-lg border border-border bg-muted/30 px-4 py-3"
                    >
                      <div className="min-w-0 flex-1 space-y-1">
                        <div className="flex items-center gap-2">
                          <Badge variant="outline" className={CHIP_CLASS.primary}>
                            {capitalize(memory.category)}
                          </Badge>
                          <span
                            className="flex items-center gap-0.5"
                            title={`Importance: ${memory.importance}/10`}
                          >
                            {Array.from({ length: Math.min(5, Math.ceil(memory.importance / 2)) }).map((_, i) => (
                              <Star key={i} className="size-3 fill-warning text-warning" aria-hidden />
                            ))}
                          </span>
                        </div>
                        <p className="text-sm text-foreground">{memory.content}</p>
                        <p className="text-xs text-muted-foreground">{formatDate(memory.created_at)}</p>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => handleDeleteMemory(memory.id)}
                        disabled={deletingId === memory.id}
                        className="shrink-0 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        aria-label="Forget this"
                        title="Forget this"
                      >
                        {deletingId === memory.id ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : (
                          <Trash2 className="size-4" />
                        )}
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </PageContainer>
  );
}
