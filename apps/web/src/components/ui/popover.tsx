"use client"

import * as React from "react"
import { Popover as PopoverPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

function Popover({
  ...props
}: React.ComponentProps<typeof PopoverPrimitive.Root>) {
  return <PopoverPrimitive.Root data-slot="popover" {...props} />
}

/** `forwardRef` — see `button.tsx`. The trigger IS the anchor Radix measures,
 *  and it is also where focus returns when the popover closes. */
const PopoverTrigger = React.forwardRef<
  React.ElementRef<typeof PopoverPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Trigger>
>(function PopoverTrigger(props, ref) {
  return <PopoverPrimitive.Trigger ref={ref} data-slot="popover-trigger" {...props} />
})

const PopoverContent = React.forwardRef<
  React.ElementRef<typeof PopoverPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>
>(function PopoverContent(
  { className, align = "center", sideOffset = 4, collisionPadding = 12, ...props },
  ref
) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        ref={ref}
        data-slot="popover-content"
        align={align}
        sideOffset={sideOffset}
        // THE PRIMARY ACTION IS ALWAYS REACHABLE.
        //
        // Measured at 1512×772 with the monitor menu open: the popover was
        // 662px tall at y=150, so "Save and restart this monitor" sat at
        // y=774 — two pixels past the bottom of the viewport, with
        // `maxHeight: none`, `overflowY: visible`, no scroll container and
        // nothing below it to scroll to. The width was capped and the
        // height was not.
        //
        // Radix measures the space it actually has (`--radix-popover-
        // content-available-height`) once it is told to collide; the cap
        // below is that measurement, floored at 12rem so a popover in a
        // tiny viewport still shows something, and the panel scrolls
        // INSIDE itself. `collisionPadding` keeps it off the edge.
        collisionPadding={collisionPadding}
        avoidCollisions
        className={cn(
          "z-50 w-72 origin-(--radix-popover-content-transform-origin) rounded-md border bg-popover p-4 text-popover-foreground shadow-md outline-hidden data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
          "max-h-[max(12rem,var(--radix-popover-content-available-height))] overflow-y-auto overscroll-contain",
          className
        )}
        {...props}
      />
    </PopoverPrimitive.Portal>
  )
})

const PopoverAnchor = React.forwardRef<
  React.ElementRef<typeof PopoverPrimitive.Anchor>,
  React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Anchor>
>(function PopoverAnchor(props, ref) {
  return <PopoverPrimitive.Anchor ref={ref} data-slot="popover-anchor" {...props} />
})

const PopoverHeader = React.forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<"div">
>(function PopoverHeader({ className, ...props }, ref) {
  return (
    <div
      ref={ref}
      data-slot="popover-header"
      className={cn("flex flex-col gap-1 text-sm", className)}
      {...props}
    />
  )
})

const PopoverTitle = React.forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<"h2">
>(function PopoverTitle({ className, ...props }, ref) {
  return (
    <div
      ref={ref}
      data-slot="popover-title"
      className={cn("font-medium", className)}
      {...props}
    />
  )
})

const PopoverDescription = React.forwardRef<
  HTMLParagraphElement,
  React.ComponentPropsWithoutRef<"p">
>(function PopoverDescription({ className, ...props }, ref) {
  return (
    <p
      ref={ref}
      data-slot="popover-description"
      className={cn("text-muted-foreground", className)}
      {...props}
    />
  )
})

export {
  Popover,
  PopoverTrigger,
  PopoverContent,
  PopoverAnchor,
  PopoverHeader,
  PopoverTitle,
  PopoverDescription,
}
