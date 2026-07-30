import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

// Small status/label pill. Semantic variants map to majra's good/warn/bad/accent.
const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-eyebrow font-medium",
  {
    variants: {
      variant: {
        default: "border-line bg-raised text-muted",
        good: "border-good/40 bg-good/10 text-good",
        warn: "border-warn/40 bg-warn/10 text-warn",
        bad: "border-bad/40 bg-bad/10 text-bad",
        accent: "border-accent/40 bg-accent/10 text-accent",
        outline: "border-line text-ink",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
