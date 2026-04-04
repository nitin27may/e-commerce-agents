import { type LucideIcon } from "lucide-react";

export function EmptyState({ icon: Icon, title, description, children }: { icon: LucideIcon; title: string; description?: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="mb-4 rounded-full bg-slate-100 p-4">
        <Icon className="size-8 text-slate-400" />
      </div>
      <h3 className="text-lg font-medium text-slate-800">{title}</h3>
      {description && <p className="mt-1 text-sm text-slate-500 max-w-sm">{description}</p>}
      {children && <div className="mt-4">{children}</div>}
    </div>
  );
}
