"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
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
  X,
  TrendingUp,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TierBenefits {
  discount_pct: number;
  free_shipping_threshold: number | null;
  priority_support: boolean;
}

interface Profile {
  id: string;
  email: string;
  name: string;
  role: string;
  loyalty_tier: string;
  total_spend: number;
  member_since: string;
  order_count: number;
  review_count: number;
  tier_benefits: TierBenefits;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TIER_COLORS: Record<string, { bg: string; text: string; accent: string }> = {
  bronze: { bg: "bg-orange-50", text: "text-orange-800", accent: "#CD7F32" },
  silver: { bg: "bg-slate-100", text: "text-slate-700", accent: "#C0C0C0" },
  gold: { bg: "bg-amber-50", text: "text-amber-800", accent: "#FFD700" },
};

const ROLE_COLORS: Record<string, string> = {
  customer: "bg-sky-100 text-sky-700",
  power_user: "bg-violet-100 text-violet-700",
  seller: "bg-emerald-100 text-emerald-700",
  admin: "bg-red-100 text-red-700",
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

  useEffect(() => {
    if (user) loadProfile();
  }, [user, loadProfile]);

  if (authLoading) return null;

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-teal-600">
              <User className="size-5 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-slate-900">My Profile</h1>
              <p className="text-sm text-slate-500">
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
            <Loader2 className="size-6 animate-spin text-teal-600" />
            <span className="ml-2 text-sm text-slate-500">
              Loading profile...
            </span>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
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
                      <h2 className="text-xl font-bold text-slate-900">
                        {profile.name}
                      </h2>
                      <Badge
                        className={`${ROLE_COLORS[profile.role] ?? "bg-slate-100 text-slate-700"} border-0`}
                      >
                        {profile.role === "admin" && (
                          <Shield className="mr-1 size-3" />
                        )}
                        {formatRole(profile.role)}
                      </Badge>
                    </div>
                    <p className="mt-1 text-sm text-slate-500">
                      {profile.email}
                    </p>
                    <p className="mt-1 text-xs text-slate-400">
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
                  <CardTitle className="text-sm font-medium text-slate-500">
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
                  <CardTitle className="text-sm font-medium text-slate-500">
                    Total Spend
                  </CardTitle>
                  <DollarSign className="size-4 text-emerald-500" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-slate-900">
                    {formatCurrency(profile.total_spend)}
                  </div>
                </CardContent>
              </Card>

              {/* Orders */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-slate-500">
                    Orders
                  </CardTitle>
                  <Package className="size-4 text-sky-500" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-slate-900">
                    {profile.order_count}
                  </div>
                </CardContent>
              </Card>

              {/* Reviews */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-slate-500">
                    Reviews
                  </CardTitle>
                  <MessageSquare className="size-4 text-amber-500" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-slate-900">
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
                  <CardTitle className="text-base font-semibold text-slate-900">
                    Loyalty Benefits
                  </CardTitle>
                  <Badge
                    className={`${TIER_COLORS[profile.loyalty_tier]?.bg ?? "bg-slate-100"} ${TIER_COLORS[profile.loyalty_tier]?.text ?? "text-slate-700"} border-0 capitalize`}
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
                    <div className="flex items-center gap-2 text-sm text-slate-600">
                      <DollarSign className="size-4 text-slate-400" />
                      <span>Order Discount</span>
                    </div>
                    <span className="text-sm font-medium text-slate-900">
                      {profile.tier_benefits.discount_pct > 0
                        ? `${profile.tier_benefits.discount_pct}% on all orders`
                        : "Not available"}
                    </span>
                  </div>

                  {/* Free Shipping */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-slate-600">
                      <Package className="size-4 text-slate-400" />
                      <span>Free Shipping</span>
                    </div>
                    <span className="text-sm font-medium text-slate-900">
                      {profile.tier_benefits.free_shipping_threshold != null
                        ? `Free shipping on orders over ${formatCurrency(profile.tier_benefits.free_shipping_threshold)}`
                        : "Not available"}
                    </span>
                  </div>

                  {/* Priority Support */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-slate-600">
                      <Shield className="size-4 text-slate-400" />
                      <span>Priority Support</span>
                    </div>
                    <span className="flex items-center gap-1 text-sm font-medium">
                      {profile.tier_benefits.priority_support ? (
                        <>
                          <Check className="size-4 text-emerald-500" />
                          <span className="text-emerald-700">Included</span>
                        </>
                      ) : (
                        <>
                          <X className="size-4 text-slate-400" />
                          <span className="text-slate-500">Not available</span>
                        </>
                      )}
                    </span>
                  </div>

                  {/* Tier progress */}
                  {TIER_THRESHOLDS[profile.loyalty_tier] && (
                    <>
                      <Separator />
                      <div className="flex items-start gap-2">
                        <TrendingUp className="mt-0.5 size-4 text-teal-600" />
                        <div className="flex-1">
                          <p className="text-sm font-medium text-slate-700">
                            Progress to {TIER_THRESHOLDS[profile.loyalty_tier]!.next}
                          </p>
                          {(() => {
                            const threshold = TIER_THRESHOLDS[profile.loyalty_tier]!.amount;
                            const remaining = Math.max(0, threshold - profile.total_spend);
                            const progress = Math.min(100, (profile.total_spend / threshold) * 100);
                            return (
                              <>
                                <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-100">
                                  <div
                                    className="h-full rounded-full bg-teal-500 transition-all"
                                    style={{ width: `${progress}%` }}
                                  />
                                </div>
                                <p className="mt-1 text-xs text-slate-500">
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
                <CardTitle className="text-base font-semibold text-slate-900">
                  Account Details
                </CardTitle>
              </CardHeader>
              <Separator />
              <CardContent className="pt-6">
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-slate-600">
                      <Mail className="size-4 text-slate-400" />
                      <span>Email</span>
                    </div>
                    <span className="text-sm font-medium text-slate-900">
                      {profile.email}
                    </span>
                  </div>

                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-slate-600">
                      <Shield className="size-4 text-slate-400" />
                      <span>Role</span>
                    </div>
                    <Badge
                      className={`${ROLE_COLORS[profile.role] ?? "bg-slate-100 text-slate-700"} border-0`}
                    >
                      {formatRole(profile.role)}
                    </Badge>
                  </div>

                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-slate-600">
                      <User className="size-4 text-slate-400" />
                      <span>User ID</span>
                    </div>
                    <span className="font-mono text-xs text-slate-500">
                      {profile.id.slice(0, 8)}...{profile.id.slice(-4)}
                    </span>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
