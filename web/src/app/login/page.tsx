"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { Store } from "lucide-react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PREVIEW, TOUR_LOGIN } from "@/lib/preview/config";
import { Label } from "@/components/ui/label";

// The seeded local demo accounts (docs/demo-guide.md). A cloud deployment
// generates its own staff passwords, so there these buttons just fail to sign in.
const DEMO_ACCOUNTS = [
  { role: "Customer", email: "alice.johnson@gmail.com", password: "customer123" },
  { role: "Seller", email: "seller.demo@gmail.com", password: "seller123" },
  { role: "Support", email: "support.demo@gmail.com", password: "support123" },
  { role: "Admin", email: "admin.demo@gmail.com", password: "admin123" },
] as const;

export default function LoginPage() {
  const router = useRouter();
  const { login } = useAuth();

  // The static preview has one synthetic account, so the form arrives filled in.
  const [email, setEmail] = useState<string>(PREVIEW ? TOUR_LOGIN.email : "");
  const [password, setPassword] = useState<string>(PREVIEW ? TOUR_LOGIN.password : "");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  async function signIn(withEmail: string, withPassword: string) {
    setError("");
    setIsLoading(true);
    try {
      await login(withEmail, withPassword);
      router.push("/chat");
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Login failed. Please try again.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!email || !password) {
      setError("Email and password are required.");
      return;
    }
    await signIn(email, password);
  }

  function signInAsDemo(account: (typeof DEMO_ACCOUNTS)[number]) {
    setEmail(account.email);
    setPassword(account.password);
    void signIn(account.email, account.password);
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-background via-background to-muted px-4">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <Link
            href="/shop"
            aria-label="Go to the store"
            className="mx-auto mb-4 flex size-10 items-center justify-center rounded-xl bg-primary"
          >
            <Store className="size-5 text-primary-foreground" aria-hidden />
          </Link>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            E-Commerce Agents
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            A demo store with an AI shopping assistant
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Sign in</CardTitle>
            <CardDescription>
              Use your email and password, or pick a demo account below.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              {error && (
                <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {error}
                </div>
              )}

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  placeholder="you@example.com"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={isLoading}
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  placeholder="Your password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={isLoading}
                />
              </div>

              <Button
                type="submit"
                size="lg"
                disabled={isLoading}
                className="mt-2 w-full"
              >
                {isLoading ? "Signing in..." : "Sign in"}
              </Button>

              {!PREVIEW && (
                <div className="space-y-2.5">
                  <div className="flex items-center gap-3 text-xs text-muted-foreground">
                    <span className="h-px flex-1 bg-border" />
                    Demo accounts
                    <span className="h-px flex-1 bg-border" />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    {DEMO_ACCOUNTS.map((account) => (
                      <Button
                        key={account.email}
                        type="button"
                        variant="outline"
                        disabled={isLoading}
                        onClick={() => signInAsDemo(account)}
                        className="h-auto flex-col items-start gap-0 px-3 py-2 text-left"
                      >
                        <span className="text-sm font-medium">{account.role}</span>
                        <span className="w-full truncate text-xs font-normal text-muted-foreground">
                          {account.email}
                        </span>
                      </Button>
                    ))}
                  </div>
                </div>
              )}

              <p className="text-center text-sm text-muted-foreground">
                Don&apos;t have an account?{" "}
                <Link
                  href="/signup"
                  className="font-medium text-primary hover:underline"
                >
                  Create one
                </Link>
              </p>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
