"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { useCart } from "@/lib/cart-context";
import { api, type CartResponse, type Address } from "@/lib/api";
import { toastOrderPlaced } from "@/lib/toast";
import { formatPrice } from "@/lib/format";
import { productImageUrl } from "@/lib/images";
import Link from "next/link";
import { Button, buttonVariants } from "@/components/ui/button";
import { PageContainer, PageHeader } from "@/components/ui/page-header";
import { TONE_TEXT_CLASS } from "@/components/ui/status-badge";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";
import {
  ArrowLeft,
  CreditCard,
  MapPin,
  Truck,
  Loader2,
  Package,
  Tag,
  Wallet,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AddressForm {
  name: string;
  street: string;
  city: string;
  state: string;
  zip: string;
  country: string;
  phone: string;
}

const EMPTY_ADDRESS: AddressForm = {
  name: "",
  street: "",
  city: "",
  state: "",
  zip: "",
  country: "US",
  phone: "",
};

function addressFromApi(addr: Address | null): AddressForm {
  if (!addr) return { ...EMPTY_ADDRESS };
  return {
    name: addr.name ?? "",
    street: addr.street ?? "",
    city: addr.city ?? "",
    state: addr.state ?? "",
    zip: addr.zip ?? "",
    country: addr.country ?? "US",
    phone: addr.phone ?? "",
  };
}

// ---------------------------------------------------------------------------
// Address form component
// ---------------------------------------------------------------------------

function AddressFormFields({
  value,
  onChange,
  idPrefix,
}: {
  value: AddressForm;
  onChange: (addr: AddressForm) => void;
  idPrefix: string;
}) {
  function update(field: keyof AddressForm, val: string) {
    onChange({ ...value, [field]: val });
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="sm:col-span-2">
        <Label htmlFor={`${idPrefix}-name`}>Full name</Label>
        <Input
          id={`${idPrefix}-name`}
          placeholder="John Doe"
          value={value.name}
          onChange={(e) => update("name", e.target.value)}
          className="mt-1.5"
        />
      </div>
      <div className="sm:col-span-2">
        <Label htmlFor={`${idPrefix}-street`}>Street address</Label>
        <Input
          id={`${idPrefix}-street`}
          placeholder="123 Main St"
          value={value.street}
          onChange={(e) => update("street", e.target.value)}
          className="mt-1.5"
        />
      </div>
      <div>
        <Label htmlFor={`${idPrefix}-city`}>City</Label>
        <Input
          id={`${idPrefix}-city`}
          placeholder="San Francisco"
          value={value.city}
          onChange={(e) => update("city", e.target.value)}
          className="mt-1.5"
        />
      </div>
      <div>
        <Label htmlFor={`${idPrefix}-state`}>State</Label>
        <Input
          id={`${idPrefix}-state`}
          placeholder="CA"
          value={value.state}
          onChange={(e) => update("state", e.target.value)}
          className="mt-1.5"
        />
      </div>
      <div>
        <Label htmlFor={`${idPrefix}-zip`}>ZIP code</Label>
        <Input
          id={`${idPrefix}-zip`}
          placeholder="94102"
          value={value.zip}
          onChange={(e) => update("zip", e.target.value)}
          className="mt-1.5"
        />
      </div>
      <div>
        <Label htmlFor={`${idPrefix}-country`}>Country</Label>
        <Input
          id={`${idPrefix}-country`}
          placeholder="US"
          value={value.country}
          onChange={(e) => update("country", e.target.value)}
          className="mt-1.5"
        />
      </div>
      <div className="sm:col-span-2">
        <Label htmlFor={`${idPrefix}-phone`}>
          Phone <span className="text-muted-foreground">(optional)</span>
        </Label>
        <Input
          id={`${idPrefix}-phone`}
          placeholder="+1 (555) 123-4567"
          value={value.phone}
          onChange={(e) => update("phone", e.target.value)}
          className="mt-1.5"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function CheckoutPage() {
  const router = useRouter();
  const { user, isLoading: authLoading } = useAuth();
  const { refreshCart } = useCart();

  const [cart, setCart] = useState<CartResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Address state
  const [shipping, setShipping] = useState<AddressForm>({ ...EMPTY_ADDRESS });
  const [billing, setBilling] = useState<AddressForm>({ ...EMPTY_ADDRESS });
  const [billingSame, setBillingSame] = useState(true);

  // Checkout state
  const [placing, setPlacing] = useState(false);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);

  // Load cart on mount
  const loadCart = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getCart();
      setCart(data);

      // Pre-populate addresses if set on cart
      if (data.shipping_address) {
        setShipping(addressFromApi(data.shipping_address));
      }
      if (data.billing_address) {
        setBilling(addressFromApi(data.billing_address));
      }
      if (data.billing_same_as_shipping !== undefined) {
        setBillingSame(data.billing_same_as_shipping);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load cart");
    } finally {
      setLoading(false);
    }
  }, []);

  useLoadEffect(loadCart, Boolean(user));

  if (authLoading || !user) return null;

  // -- Validation --

  function validateShipping(): string | null {
    if (!shipping.street.trim()) return "Street address is required";
    if (!shipping.city.trim()) return "City is required";
    if (!shipping.state.trim()) return "State is required";
    if (!shipping.zip.trim()) return "ZIP code is required";
    return null;
  }

  function validateBilling(): string | null {
    if (billingSame) return null;
    if (!billing.street.trim()) return "Billing street address is required";
    if (!billing.city.trim()) return "Billing city is required";
    if (!billing.state.trim()) return "Billing state is required";
    if (!billing.zip.trim()) return "Billing ZIP code is required";
    return null;
  }

  // -- Place order --

  async function handlePlaceOrder() {
    setCheckoutError(null);

    const shippingErr = validateShipping();
    if (shippingErr) {
      setCheckoutError(shippingErr);
      return;
    }
    const billingErr = validateBilling();
    if (billingErr) {
      setCheckoutError(billingErr);
      return;
    }

    setPlacing(true);
    try {
      const shippingAddr: Address = {
        name: shipping.name || undefined,
        street: shipping.street,
        city: shipping.city,
        state: shipping.state,
        zip: shipping.zip,
        country: shipping.country || "US",
        phone: shipping.phone || undefined,
      };

      const billingAddr: Address | null = billingSame
        ? null
        : {
            name: billing.name || undefined,
            street: billing.street,
            city: billing.city,
            state: billing.state,
            zip: billing.zip,
            country: billing.country || "US",
            phone: billing.phone || undefined,
          };

      const result = await api.checkout({
        shipping_address: shippingAddr,
        billing_address: billingAddr,
        billing_same_as_shipping: billingSame,
      });

      // Refresh the cart context (cart is now empty)
      await refreshCart();
      toastOrderPlaced(result.order_id);
      router.push(`/orders/${result.order_id}?placed=true`);
    } catch (err) {
      setCheckoutError(
        err instanceof Error ? err.message : "Checkout failed. Please try again."
      );
    } finally {
      setPlacing(false);
    }
  }

  const backLink = (
    <Link
      href="/cart"
      className={cn(buttonVariants({
        variant: "ghost",
        size: "sm",
        className: "-ml-2 mb-4 text-muted-foreground",
      }))}
    >
      <ArrowLeft data-icon="inline-start" />
      Back to cart
    </Link>
  );

  // -- Loading state --

  if (loading) {
    return (
      <PageContainer>
        {backLink}
        <PageHeader icon={CreditCard} title="Checkout" description="Loading your order..." />
        <div className="flex items-center justify-center py-32">
          <Loader2 className="size-8 animate-spin text-muted-foreground" />
        </div>
      </PageContainer>
    );
  }

  // -- Error loading cart --

  if (error) {
    return (
      <PageContainer>
        {backLink}
        <PageHeader icon={CreditCard} title="Checkout" />
        <ErrorState inline message={error} onRetry={loadCart} homeHref="/cart" homeLabel="Back to cart" />
      </PageContainer>
    );
  }

  // -- Empty cart --

  if (!cart || cart.items.length === 0) {
    return (
      <PageContainer>
        {backLink}
        <PageHeader icon={CreditCard} title="Checkout" />
        <EmptyState
          icon={Package}
          title="Your cart is empty"
          description="Add some products before checking out."
        >
          <Button onClick={() => router.push("/products")}>Browse products</Button>
        </EmptyState>
      </PageContainer>
    );
  }

  return (
    <PageContainer>
      {backLink}
      <PageHeader
        icon={CreditCard}
        title="Checkout"
        description={`Review and place your order (${cart.item_count} item${cart.item_count !== 1 ? "s" : ""})`}
      />

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="space-y-6 lg:col-span-3">
          {/* Shipping address */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <MapPin className="size-4 text-muted-foreground" aria-hidden />
                Shipping address
              </CardTitle>
            </CardHeader>
            <CardContent>
              <AddressFormFields value={shipping} onChange={setShipping} idPrefix="ship" />
            </CardContent>
          </Card>

          {/* Billing address */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CreditCard className="size-4 text-muted-foreground" aria-hidden />
                Billing address
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <label className="flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  checked={billingSame}
                  onChange={(e) => setBillingSame(e.target.checked)}
                  className="size-4 rounded border-border accent-primary"
                />
                <span className="text-sm text-foreground">Same as shipping address</span>
              </label>
              {!billingSame && (
                <AddressFormFields value={billing} onChange={setBilling} idPrefix="bill" />
              )}
            </CardContent>
          </Card>

          {/* Payment */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Wallet className="size-4 text-muted-foreground" aria-hidden />
                Payment
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center gap-4 rounded-lg border border-border bg-muted/50 px-4 py-3">
                <div className="flex h-9 w-12 shrink-0 items-center justify-center rounded-md bg-card ring-1 ring-foreground/10">
                  <CreditCard className="size-5 text-muted-foreground" aria-hidden />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="font-mono text-sm font-medium tracking-wider text-foreground">
                    4242 4242 4242 4242
                  </p>
                  <p className="text-xs text-muted-foreground">Demo card · expires 12/99</p>
                </div>
              </div>
              <p className="mt-3 text-xs text-muted-foreground">
                This is a demo store. No real payment is processed.
              </p>
            </CardContent>
          </Card>
        </div>

        {/* Order summary */}
        <div className="lg:col-span-2 lg:sticky lg:top-6 lg:self-start">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Package className="size-4 text-muted-foreground" aria-hidden />
                Order summary
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="divide-y divide-border">
                {cart.items.map((item) => (
                  <div key={item.id} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={productImageUrl(item.product_id, 96, 96, item.image_url, item.category, item.name)}
                      alt={item.name}
                      className="size-12 shrink-0 rounded-md bg-muted object-cover"
                      loading="lazy"
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-foreground">{item.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {item.quantity} × {formatPrice(item.price)}
                      </p>
                    </div>
                    <span className="shrink-0 text-sm font-medium tabular-nums text-foreground">
                      {formatPrice(item.subtotal)}
                    </span>
                  </div>
                ))}
              </div>

              <Separator />

              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">Subtotal</span>
                  <span className="tabular-nums text-foreground">{formatPrice(cart.subtotal)}</span>
                </div>
                {cart.coupon_code && cart.discount_amount > 0 && (
                  <div className="flex items-center justify-between text-sm">
                    <span className="flex items-center gap-1.5 text-muted-foreground">
                      <Tag className={cn("size-3", TONE_TEXT_CLASS.success)} aria-hidden />
                      Coupon <span className="font-mono">{cart.coupon_code}</span>
                    </span>
                    <span className={cn("tabular-nums", TONE_TEXT_CLASS.success)}>
                      -{formatPrice(cart.discount_amount)}
                    </span>
                  </div>
                )}
                <Separator />
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-muted-foreground">Total</span>
                  <span className="text-xl font-semibold tabular-nums text-foreground">
                    {formatPrice(cart.total)}
                  </span>
                </div>
              </div>

              {checkoutError && (
                <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                  {checkoutError}
                </div>
              )}

              <Button className="w-full" size="xl" disabled={placing} onClick={handlePlaceOrder}>
                {placing ? (
                  <>
                    <Loader2 className="animate-spin" data-icon="inline-start" />
                    Placing order...
                  </>
                ) : (
                  <>
                    <Truck data-icon="inline-start" />
                    Place order · {formatPrice(cart.total)}
                  </>
                )}
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </PageContainer>
  );
}
