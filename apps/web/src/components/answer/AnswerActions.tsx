"use client";

import { Check, ClipboardCopy, Download, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { copyText, downloadTextFile, exportFilename } from "@/lib/export";
import { cn } from "@/lib/utils";

/** How long the "Copied" / "Saved" acknowledgement stays on screen. */
const ACK_MS = 2000;

type Ack = "idle" | "done" | "failed";

/**
 * A button that puts something in the analyst's hands and then says
 * whether it worked.
 *
 * The acknowledgement is not decoration. `navigator.clipboard` is
 * unavailable on insecure origins and in some embedded browsers, and a
 * button that always flashes "Copied" over a clipboard that did not change
 * is worse than one that sometimes admits it could not — the analyst walks
 * into the meeting with the previous contents of their clipboard.
 */
export function ExportButton({
  label,
  doneLabel,
  title,
  kind,
  onRun,
  className,
}: {
  label: string;
  doneLabel: string;
  title: string;
  kind: "copy" | "download";
  /** Returns false when the action could not complete. */
  onRun: () => Promise<boolean> | boolean;
  className?: string;
}) {
  const [ack, setAck] = useState<Ack>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const run = useCallback(async () => {
    const ok = await onRun();
    setAck(ok ? "done" : "failed");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setAck("idle"), ACK_MS);
  }, [onRun]);

  const Icon =
    ack === "done" ? Check : ack === "failed" ? TriangleAlert : kind === "copy" ? ClipboardCopy : Download;

  return (
    <Button
      variant="ghost"
      size="xs"
      title={title}
      onClick={() => void run()}
      className={cn(
        "h-5 gap-1 rounded-full px-2 text-[0.68rem] font-normal text-muted-foreground hover:text-foreground",
        ack === "done" && "text-verified hover:text-verified",
        ack === "failed" && "text-warning hover:text-warning",
        className,
      )}
    >
      <Icon className="size-3" />
      {/* The live region is on the button's own label so the outcome is
          announced without moving focus or opening a toast nobody asked
          for. */}
      <span aria-live="polite">
        {ack === "done"
          ? doneLabel
          : ack === "failed"
            ? kind === "copy"
              ? "Couldn’t copy — select the text instead"
              : "Couldn’t save the file"
            : label}
      </span>
    </Button>
  );
}

/** Copy plain text, with the browser's refusal surfaced rather than eaten. */
export function CopyTextButton({
  label,
  doneLabel = "Copied",
  title,
  text,
  className,
}: {
  label: string;
  doneLabel?: string;
  title: string;
  text: () => string;
  className?: string;
}) {
  return (
    <ExportButton
      kind="copy"
      label={label}
      doneLabel={doneLabel}
      title={title}
      className={className}
      onRun={() => copyText(text())}
    />
  );
}

/** Save a CSV entirely client-side — no upload, no round trip. */
export function DownloadCsvButton({
  label,
  doneLabel = "Saved",
  title,
  filenameKind,
  filenameTag,
  csv,
  className,
}: {
  label: string;
  doneLabel?: string;
  title: string;
  filenameKind: string;
  filenameTag?: string;
  csv: () => string;
  className?: string;
}) {
  return (
    <ExportButton
      kind="download"
      label={label}
      doneLabel={doneLabel}
      title={title}
      className={className}
      onRun={() => {
        downloadTextFile(exportFilename(filenameKind, filenameTag, "csv"), "text/csv", csv());
        return true;
      }}
    />
  );
}
