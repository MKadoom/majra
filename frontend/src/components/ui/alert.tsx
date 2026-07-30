import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const alertVariants = cva(
  "relative w-full rounded-md border px-3 py-2 text-sm [&>svg]:me-2 [&>svg]:inline",
  {
    variants: {
      variant: {
        default: "border-line bg-surface text-ink",
        bad: "border-bad/40 bg-bad/10 text-bad",
        warn: "border-warn/40 bg-warn/10 text-warn",
        good: "border-good/40 bg-good/10 text-good",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

const Alert = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & VariantProps<typeof alertVariants>
>(({ className, variant, ...props }, ref) => (
  <div ref={ref} role="alert" className={cn(alertVariants({ variant }), className)} {...props} />
));
Alert.displayName = "Alert";

export { Alert, alertVariants };
