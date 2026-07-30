import * as React from "react";

import { cn } from "@/lib/utils";

// Uses the shared .field class so native inputs and shadcn controls stay in sync.
const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input ref={ref} type={type} className={cn("field", className)} {...props} />
  ),
);
Input.displayName = "Input";

export { Input };
