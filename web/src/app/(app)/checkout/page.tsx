"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { useCart } from "@/lib/cart-context";
import { api, type CartResponse, type Address } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import { productImageUrl } from "@/lib/images";
import { Button } from "@/components/ui/button";
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
  CheckCircle,
  Loader2,
  Package,
  Tag,
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
        <Label htmlFor={`${idPrefix}-name`}>Full Name</Label>
        <Input
          id={`${idPrefix}-name`}
          placeholder="John Doe"
          value={value.name}
          onChange={(e) => update("name", e.target.value)}
          className="mt-1.5"
        />
      </div>
      <div className="sm:col-span-2">
        <Label htmlFor={`${idPrefix}-street`}>Street Address</Label>
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
        <Label htmlFor={`${idPrefix}-zip`}>ZIP Code</Label>
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
          Phone <span className="text-slate-400">(optional)</span>
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

  useEffect(() => {
    if (user) loadCart();
  }, [user, loadCart]);

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

      // Redirect to order detail
      router.push(`/orders/${result.order_id}?placed=true`);
    } catch (err) {
      setCheckoutError(
        err instanceof Error ? err.message : "Checkout failed. Please try again."
      );
    } finally {
      setPlacing(false);
    }
  }

  // -- Loading state --

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50">
        <div className="border-b border-slate-200 bg-white">
          <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6 lg:px-8">
            <Button
              variant="ghost"
              size="sm"
              className="-ml-2 text-slate-500 hover:text-slate-700"
              onClick={() => router.push("/cart")}
            >
              <ArrowLeft className="mr-1.5 size-4" />
              Back to Cart
            </Button>
          </div>
        </div>
        <div className="flex items-center justify-center py-32">
          <Loader2 className="size-8 animate-spin text-slate-400" />
        </div>
      </div>
    );
  }

  // -- Empty cart --

  if (!cart || cart.items.length === 0) {
    return (
      <div className="min-h-screen bg-slate-50">
        <div className="border-b border-slate-200 bg-white">
          <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6 lg:px-8">
            <Button
              variant="ghost"
              size="sm"
              className="-ml-2 text-slate-500 hover:text-slate-700"
              onClick={() => router.push("/cart")}
            >
              <ArrowLeft className="mr-1.5 size-4" />
              Back to Cart
            </Button>
          </div>
        </div>
        <div className="mx-auto max-w-4xl px-4 py-20 text-center sm:px-6 lg:px-8">
          <Package className="mx-auto size-12 text-slate-300" />
          <h2 className="mt-4 text-lg font-semibold text-slate-700">
            Your cart is empty
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Add some products before checking out.
          </p>
          <Button
            className="mt-6 bg-teal-600 hover:bg-teal-700"
            onClick={() => router.push("/products")}
          >
            Browse Products
          </Button>
        </div>
      </div>
    );
  }

  // -- Error loading cart --

  if (error) {
    return (
      <div className="min-h-screen bg-slate-50">
        <div className="border-b border-slate-200 bg-white">
          <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6 lg:px-8">
            <Button
              variant="ghost"
              size="sm"
              className="-ml-2 text-slate-500 hover:text-slate-700"
              onClick={() => router.push("/cart")}
            >
              <ArrowLeft className="mr-1.5 size-4" />
              Back to Cart
            </Button>
          </div>
        </div>
        <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6 lg:px-8">
          <Button
            variant="ghost"
            size="sm"
            className="-ml-2 text-slate-500 hover:text-slate-700"
            onClick={() => router.push("/cart")}
          >
            <ArrowLeft className="mr-1.5 size-4" />
            Back to Cart
          </Button>
          <div className="mt-4 flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-teal-600">
              <CheckCircle className="size-5 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-slate-900">Checkout</h1>
              <p className="text-sm text-slate-500">
                Complete your order ({cart.item_count} item{cart.item_count !== 1 ? "s" : ""})
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 lg:px-8">
        <div className="space-y-6">
          {/* Section 1: Shipping Address */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <MapPin className="size-4 text-slate-400" />
                Shipping Address
              </CardTitle>
            </CardHeader>
            <CardContent>
              <AddressFormFields
                value={shipping}
                onChange={setShipping}
                idPrefix="ship"
              />
            </CardContent>
          </Card>

          {/* Section 2: Billing Address */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CreditCard className="size-4 text-slate-400" />
                Billing Address
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={billingSame}
                  onChange={(e) => setBillingSame(e.target.checked)}
                  className="size-4 rounded border-slate-300 text-teal-600 focus:ring-teal-500"
                />
                <span className="text-sm text-slate-700">
                  Same as shipping address
                </span>
              </label>
              {!billingSame && (
                <AddressFormFields
                  value={billing}
                  onChange={setBilling}
                  idPrefix="bill"
                />
              )}
            </CardContent>
          </Card>

          {/* Section 3: Order Review */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Package className="size-4 text-slate-400" />
                Order Review
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Item list */}
              <div className="divide-y divide-slate-100">
                {cart.items.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-center gap-3 py-3 first:pt-0 last:pb-0"
                  >
                    <img
                      src={productImageUrl(item.product_id, 48, 48, item.image_url, item.category)}
                      alt={item.name}
                      className="size-12 shrink-0 rounded-md object-cover bg-slate-100"
                      loading="lazy"
                    />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-slate-800 truncate">
                        {item.name}
                      </p>
                      <p className="text-xs text-slate-500">
                        {item.quantity} x {formatPrice(item.price)}
                      </p>
                    </div>
                    <span className="text-sm font-medium text-slate-900 shrink-0">
                      {formatPrice(item.subtotal)}
                    </span>
                  </div>
                ))}
              </div>

              <Separator />

              {/* Totals */}
              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-slate-500">Subtotal</span>
                  <span className="text-slate-700">
                    {formatPrice(cart.subtotal)}
                  </span>
                </div>
                {cart.coupon_code && cart.discount_amount > 0 && (
                  <div className="flex items-center justify-between text-sm">
                    <span className="flex items-center gap-1.5 text-slate-500">
                      <Tag className="size-3 text-emerald-500" />
                      Coupon ({cart.coupon_code})
                    </span>
                    <span className="text-emerald-600">
                      -{formatPrice(cart.discount_amount)}
                    </span>
                  </div>
                )}
                <Separator />
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-700">
                    Total
                  </span>
                  <span className="text-xl font-bold text-slate-900">
                    {formatPrice(cart.total)}
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Section 4: Payment */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CreditCard className="size-4 text-slate-400" />
                Payment
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="rounded-xl border border-slate-200 bg-gradient-to-br from-slate-800 to-slate-900 p-6 text-white">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium uppercase tracking-wider text-slate-400">
                    Demo Payment
                  </span>
                  <CreditCard className="size-6 text-slate-400" />
                </div>
                <div className="mt-6 font-mono text-lg tracking-widest">
                  4242 4242 4242 4242
                </div>
                <div className="mt-4 flex items-center justify-between text-xs text-slate-400">
                  <span>DEMO CARD</span>
                  <span>12/99</span>
                </div>
              </div>
              <p className="mt-3 text-center text-xs text-slate-500">
                This is a demo application. No real payment will be processed.
              </p>
            </CardContent>
          </Card>

          {/* Error message */}
          {checkoutError && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {checkoutError}
            </div>
          )}

          {/* Place Order button */}
          <Button
            className="w-full bg-teal-600 hover:bg-teal-700"
            size="lg"
            disabled={placing}
            onClick={handlePlaceOrder}
          >
            {placing ? (
              <>
                <Loader2 className="mr-2 size-4 animate-spin" />
                Processing Order...
              </>
            ) : (
              <>
                <Truck className="mr-2 size-4" />
                Place Order -- {formatPrice(cart.total)}
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
