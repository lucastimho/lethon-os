import * as React from "react";
import { cn } from "@/lib/utils";

type Variant = "default" | "ghost" | "outline" | "danger";
type Size = "default" | "sm" | "icon";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

const base =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium " +
  "transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] " +
  "disabled:pointer-events-none disabled:opacity-50";

const variants: Record<Variant, string> = {
  default:
    "bg-[var(--color-accent)] text-[var(--color-accent-foreground)] hover:brightness-110 " +
    "shadow-[0_0_20px_-4px_var(--color-accent)]",
  ghost:
    "text-foreground/80 hover:bg-white/5 hover:text-foreground",
  outline:
    "border border-border text-foreground/90 hover:bg-white/5 hover:border-[var(--color-accent)]/60",
  danger:
    "bg-[var(--color-danger)]/90 text-white hover:bg-[var(--color-danger)]",
};

const sizes: Record<Size, string> = {
  default: "h-9 px-4 py-2",
  sm: "h-8 px-3 text-xs",
  icon: "h-8 w-8",
};

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "default", ...props }, ref) => (
    <button
      ref={ref}
      className={cn(base, variants[variant], sizes[size], className)}
      {...props}
    />
  ),
);
Button.displayName = "Button";
