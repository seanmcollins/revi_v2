import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * `forwardRef` (see `button.tsx`) — and this is the one primitive where the
 * ref is passed DIRECTLY rather than through a Slot: `TurnInput` holds a
 * `composerRef` and calls `.focus()` on it the moment the pipeline frees,
 * because disabling a focused textarea drops the caret on `<body>`. Under
 * React 18 without this, that ref is `null` and a keyboard-only analyst
 * Tabs back into the composer after every answer.
 */
const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.ComponentPropsWithoutRef<"textarea">
>(function Textarea({ className, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      data-slot="textarea"
      className={cn(
        "flex field-sizing-content min-h-16 w-full rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 md:text-sm",
        className
      )}
      {...props}
    />
  )
})

export { Textarea }
