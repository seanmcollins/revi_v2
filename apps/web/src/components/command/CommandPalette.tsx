"use client";

import {
  ArrowUpRight,
  CircleHelp,
  FileSearch,
  MessageSquareText,
  Play,
  Repeat,
  RotateCcw,
  Search,
  Sparkles,
  SunMoon,
} from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Dialog as DialogPrimitive } from "radix-ui";

import { resolveDriverKind } from "@/lib/apiDriver";
import { GUIDE_QUESTIONS } from "@/lib/guideQuestions";
import { REFERENCE_QUESTIONS } from "@/lib/mockDriver";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

interface PaletteAction {
  id: string;
  group: string;
  label: string;
  hint?: string;
  icon: ReactNode;
  disabled?: boolean;
  run: () => void;
}

/**
 * ⌘K command palette — keyboard-first control surface (Linear DNA).
 * Investigate (ask / replay), navigate (turns, findings, evidence),
 * workspace (theme, driver, reset). Opens with ⌘K / Ctrl+K.
 */
export function CommandPalette({
  open,
  onOpenChange,
  onReplay,
  replayDisabled,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onReplay: () => void;
  replayDisabled: boolean;
}) {
  const { resolvedTheme, setTheme } = useTheme();
  const submit = useSessionStore((s) => s.submit);
  const streaming = useSessionStore((s) => s.streamingTurnId !== null);
  const turns = useSessionStore((s) => s.turns);
  const referents = useSessionStore((s) => s.referents);
  const reset = useSessionStore((s) => s.reset);
  const openDrawer = useSessionStore((s) => s.openDrawer);

  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Closing resets the filter — handled in the transition itself, never
  // in an effect (no cascading renders).
  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setQuery("");
      setSelected(0);
    }
    onOpenChange(next);
  };

  // Global ⌘K / Ctrl+K toggle.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (open) {
          handleOpenChange(false);
        } else {
          onOpenChange(true);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const answered = turns.filter((t) =>
    REFERENCE_QUESTIONS.includes(t.submission.utterance ?? ""),
  ).length;
  const nextQuestion = REFERENCE_QUESTIONS[answered];

  const close = () => handleOpenChange(false);

  const scrollToTurn = (turnId: string) => {
    document
      .getElementById(`lineage-turn-${turnId}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const actions = useMemo<PaletteAction[]>(() => {
    const driverKind = resolveDriverKind();
    const list: PaletteAction[] = [];

    if (nextQuestion) {
      list.push({
        id: "ask-next",
        group: "Investigate",
        label: `Ask: ${nextQuestion}`,
        hint: "next in the reference drill-down",
        icon: <MessageSquareText className="size-3.5" />,
        disabled: streaming,
        run: () => void submit({ utterance: nextQuestion }),
      });
    }
    list.push(
      {
        id: "ask-pr3",
        group: "Investigate",
        label: "Ask: What is PR3?",
        hint: "definitional",
        icon: <CircleHelp className="size-3.5" />,
        disabled: streaming,
        run: () => void submit({ utterance: "What is PR3?" }),
      },
      {
        id: "replay",
        group: "Investigate",
        label: "Replay reference demo",
        hint: "five turns",
        icon: <Play className="size-3.5" />,
        disabled: replayDisabled,
        run: onReplay,
      },
      {
        id: "focus-composer",
        group: "Investigate",
        label: "New investigation",
        hint: "focus the composer",
        icon: <ArrowUpRight className="size-3.5" />,
        run: () => document.getElementById("turn-composer")?.focus(),
      },
    );

    // The hero's guide questions, searchable without leaving the keyboard.
    for (const question of GUIDE_QUESTIONS) {
      if (question === "What is PR3?") continue; // already listed above
      list.push({
        id: `guide-${question}`,
        group: "Guide questions",
        label: `Ask: ${question}`,
        icon: <Sparkles className="size-3.5" />,
        disabled: streaming,
        run: () => void submit({ utterance: question }),
      });
    }

    turns.forEach((turn, i) => {
      const label =
        turn.submission.utterance ??
        turn.submission.clarificationResponse ??
        "(typed refinement)";
      list.push({
        id: `turn-${turn.id}`,
        group: "Navigate",
        label: `T${i + 1} — ${label}`,
        icon: <Search className="size-3.5" />,
        run: () => scrollToTurn(turn.id),
      });
    });
    for (const entry of Object.values(referents)) {
      if (entry.referent.kind !== "finding") continue;
      list.push({
        id: `ref-${entry.referent.value}`,
        group: "Navigate",
        label: `${entry.referent.value} — ${entry.label}`,
        icon: <Search className="size-3.5" />,
        run: () => {
          useSessionStore.getState().focusReferent(entry.referent.value);
          document
            .getElementById(`referent-${entry.referent.value}`)
            ?.scrollIntoView({ behavior: "smooth", block: "center" });
        },
      });
    }
    const evidenced = [...turns].reverse().find((t) => t.answer.evidence);
    if (evidenced) {
      list.push({
        id: "evidence",
        group: "Navigate",
        label: "Open evidence drawer",
        hint: "latest answer",
        icon: <FileSearch className="size-3.5" />,
        run: () => openDrawer(evidenced.id),
      });
    }

    list.push(
      {
        id: "theme",
        group: "Workspace",
        label: `Switch to ${resolvedTheme === "dark" ? "light" : "dark"} theme`,
        icon: <SunMoon className="size-3.5" />,
        run: () => setTheme(resolvedTheme === "dark" ? "light" : "dark"),
      },
      {
        id: "driver",
        group: "Workspace",
        label: `Switch to ${driverKind === "api" ? "mock" : "live API"} driver`,
        hint: "reloads the page",
        icon: <Repeat className="size-3.5" />,
        run: () => {
          window.localStorage.setItem("revi-driver", driverKind === "api" ? "mock" : "api");
          window.location.reload();
        },
      },
      {
        id: "reset",
        group: "Workspace",
        label: "Reset session",
        icon: <RotateCcw className="size-3.5" />,
        disabled: turns.length === 0,
        run: reset,
      },
    );
    return list;
  }, [
    nextQuestion,
    streaming,
    replayDisabled,
    onReplay,
    submit,
    turns,
    referents,
    openDrawer,
    resolvedTheme,
    setTheme,
    reset,
  ]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return actions;
    return actions.filter(
      (a) => a.label.toLowerCase().includes(q) || a.group.toLowerCase().includes(q),
    );
  }, [actions, query]);

  const clampedSelected = Math.min(selected, Math.max(filtered.length - 1, 0));

  const runAction = (action: PaletteAction) => {
    if (action.disabled) return;
    close();
    action.run();
  };

  const onInputKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelected((s) => (s + 1) % Math.max(filtered.length, 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelected((s) => (s - 1 + Math.max(filtered.length, 1)) % Math.max(filtered.length, 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const action = filtered[clampedSelected];
      if (action) runAction(action);
    }
  };

  // Keep the selected row in view while arrowing.
  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-index="${clampedSelected}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [clampedSelected]);

  let lastGroup = "";

  return (
    <DialogPrimitive.Root open={open} onOpenChange={handleOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="overlay-in fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px]" />
        <DialogPrimitive.Content
          className="panel-in fixed left-1/2 top-[18%] z-50 w-[34rem] max-w-[calc(100vw-2rem)] -translate-x-1/2 overflow-hidden rounded-xl border bg-surface-overlay shadow-2xl shadow-black/20"
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            inputRef.current?.focus();
          }}
        >
          <DialogPrimitive.Title className="sr-only">Command palette</DialogPrimitive.Title>
          <div className="flex items-center gap-2.5 border-b px-3.5">
            <Search className="size-4 shrink-0 text-muted-foreground" />
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSelected(0);
              }}
              onKeyDown={onInputKeyDown}
              placeholder="Ask, navigate, or act…"
              className="h-11 w-full bg-transparent text-[0.85rem] outline-none placeholder:text-muted-foreground/60"
            />
            <kbd className="rounded border bg-surface-sunken px-1.5 py-0.5 font-mono text-[0.6rem] text-muted-foreground">
              esc
            </kbd>
          </div>
          <div ref={listRef} className="max-h-[19rem] overflow-y-auto p-1.5">
            {filtered.length === 0 && (
              <p className="px-3 py-6 text-center text-[0.72rem] text-muted-foreground">
                Nothing matches “{query}”.
              </p>
            )}
            {filtered.map((action, i) => {
              const showGroup = action.group !== lastGroup;
              lastGroup = action.group;
              return (
                <div key={action.id}>
                  {showGroup && (
                    <p className="px-2.5 pb-1 pt-2.5 text-[0.58rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
                      {action.group}
                    </p>
                  )}
                  <button
                    type="button"
                    data-index={i}
                    disabled={action.disabled}
                    onMouseEnter={() => setSelected(i)}
                    onClick={() => runAction(action)}
                    className={cn(
                      "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-[0.76rem] transition-colors duration-150",
                      i === clampedSelected && !action.disabled
                        ? "bg-accent text-foreground"
                        : "text-secondary-foreground",
                      action.disabled && "opacity-40",
                    )}
                  >
                    <span className="text-muted-foreground">{action.icon}</span>
                    <span className="min-w-0 flex-1 truncate">{action.label}</span>
                    {action.hint && (
                      <span className="shrink-0 text-[0.6rem] text-muted-foreground/70">
                        {action.hint}
                      </span>
                    )}
                  </button>
                </div>
              );
            })}
          </div>
          <div className="flex items-center gap-3 border-t px-3.5 py-2 text-[0.6rem] text-muted-foreground/70">
            <span className="flex items-center gap-1">
              <kbd className="rounded border bg-surface-sunken px-1 font-mono">↑↓</kbd> navigate
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded border bg-surface-sunken px-1 font-mono">↵</kbd> run
            </span>
            <span className="ml-auto">Revi command palette</span>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
