import {
  LayoutDashboard,
  MessageSquare,
  ShoppingBag,
  ShoppingCart,
  Package,
  User,
  BarChart3,
  Shield,
  Bot,
  CheckCircle,
  Activity,
  Lightbulb,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  adminOnly?: boolean;
  sellerOnly?: boolean;
  /** Shown for the cart count badge. */
  cartBadge?: boolean;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

/**
 * Grouped navigation, shared by the sidebar and the command palette.
 * WorkGraph-influenced grouping (Workspace / Agents / …) adapted to shopping.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Shop",
    items: [
      { label: "Home", href: "/home", icon: LayoutDashboard },
      { label: "Chat", href: "/chat", icon: MessageSquare },
      { label: "Agents", href: "/agents", icon: Bot },
      { label: "Products", href: "/products", icon: ShoppingBag },
      { label: "Cart", href: "/cart", icon: ShoppingCart, cartBadge: true },
      { label: "Orders", href: "/orders", icon: Package },
      { label: "Runs", href: "/runs", icon: Activity },
    ],
  },
  {
    label: "Account",
    items: [
      { label: "Profile", href: "/profile", icon: User },
      { label: "Seller", href: "/seller", icon: BarChart3, sellerOnly: true },
      { label: "Proposals", href: "/seller/proposals", icon: Lightbulb, sellerOnly: true },
    ],
  },
  {
    label: "Admin",
    items: [
      { label: "Overview", href: "/admin", icon: Shield, adminOnly: true },
      { label: "Approvals", href: "/admin/approvals", icon: CheckCircle, adminOnly: true },
      { label: "Usage", href: "/admin/usage", icon: BarChart3, adminOnly: true },
    ],
  },
];

/** Filter nav by the current user's role flags. */
export function visibleGroups(opts: {
  isAdmin: boolean;
  isSeller: boolean;
}): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => {
      if (item.adminOnly) return opts.isAdmin;
      if (item.sellerOnly) return opts.isSeller;
      return true;
    }),
  })).filter((group) => group.items.length > 0);
}

/** A flat label for the current path, used by the top-bar breadcrumb. */
export function labelForPath(pathname: string): string {
  const flat = NAV_GROUPS.flatMap((g) => g.items);
  // Longest matching href wins (so /admin/usage beats /admin).
  const match = flat
    .filter((i) => pathname === i.href || pathname.startsWith(i.href + "/"))
    .sort((a, b) => b.href.length - a.href.length)[0];
  return match?.label ?? "Home";
}
