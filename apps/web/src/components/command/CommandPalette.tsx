"use client";

import {
  ArrowUpRight,
  CircleHelp,
  FileSearch,
  MessageSquarePlus,
  MessageSquareText,
  Play,
  Repeat,
  RotateCcw,
  LayoutTemplate,
  Search,
  Settings2,
  Sparkles,
  SunMoon,
} from "lucide-react";
import { useTheme } from "next-themes";
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Dialog as DialogPrimitive } from "radix-ui";

import { resolveDriverKind } from "@/lib/apiDriver";
import {
  ANSWER_VARIANT_HINTS,
  ANSWER_VARIANT_LABELS,
  nextAnswerVariant,
  setAnswerVariant,
} from "@/lib/answerVariant";
import { untitledTurnLabel } from "@/lib/format";
import { useAnswerVariant } from "@/lib/useAnswerVariant";
import { GUIDE_QUESTIONS } from "@/lib/guideQuestions";
import { REFERENCE_QUESTIONS } from "@/lib/mock/reference";
import { useSessionStore } from "@/lib/store";
import { scrollIntoViewRespectingMotion } from "@/lib/useReducedMotion";
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
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { resolvedTheme, setTheme } = useTheme();
  const submit = useSessionStore((s) => s.submit);
  const streaming = useSessionStore((s) => s.streamingTurnId !== null);
  const newChatPending = useSessionStore((s) => s.newChatPending);
  const turns = useSessionStore((s) => s.turns);
  const referents = useSessionStore((s) => s.referents);
  const newChat = useSessionStore((s) => s.newChat);
  const openDrawer = useSessionStore((s) => s.openDrawer);
  const replayReference = useSessionStore((s) => s.replayReference);
  const replaying = useSessionStore((s) => s.replaying);
  const replayProgress = useSessionStore((s) => s.replayProgress);
  const openSettings = useSessionStore((s) => s.openSettings);
  const debug = useSessionStore((s) => s.settings.debug);
  const switchingSessionId = useSessionStore((s) => s.switchingSessionId);
  // The A/B toggle. No reload: the answer surface is a pure function of
  // the turns already in the store, so a reviewer can flip layouts on the
  // thread they are judging without losing it.
  const variant = useAnswerVariant();
  // Mirrors SessionRail's identical guard: any of a turn streaming, a new
  // chat bootstrapping, a replay running, or a session switch in flight
  // means `submit()` either no-ops or would race whichever session wins.
  const newChatBusy = streaming || newChatPending || replaying || switchingSessionId !== null;
  // Why the actions that ask a question are inert right now, in the words
  // of whichever condition is actually holding them. Rendered as hint text
  // on the disabled rows: dimming says "not now" and nothing says why.
  const busyReason = streaming
    ? "wait — a turn is running"
    : replaying
      ? "wait — the demo is replaying"
      : switchingSessionId !== null
        ? "wait — a session is opening"
        : newChatPending
          ? "wait — a new chat is opening"
          : undefined;

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
    scrollIntoViewRespectingMotion(document.getElementById(`lineage-turn-${turnId}`), {
      block: "start",
    });
  };

  const actions = useMemo<PaletteAction[]>(() => {
    const driverKind = resolveDriverKind();
    // `NEXT_PUBLIC_REVI_DRIVER=mock` forced by env — the only case where the
    // palette still offers a driver switch. The mock driver is a dev/test
    // fixture, not a user-facing mode: with the default env (live API), no
    // casual control writes the `revi-driver` localStorage override.
    const envForcedMock = process.env.NEXT_PUBLIC_REVI_DRIVER === "mock";
    const list: PaletteAction[] = [];

    if (nextQuestion) {
      list.push({
        id: "ask-next",
        group: "Investigate",
        label: `Ask: ${nextQuestion}`,
        hint: "next in the reference drill-down",
        icon: <MessageSquareText className="size-3.5" />,
        disabled: newChatBusy,
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
        disabled: newChatBusy,
        run: () => void submit({ utterance: "What is PR3?" }),
      },
      {
        id: "replay",
        group: "Investigate",
        label: replayProgress
          ? `Replaying reference demo (${replayProgress.index}/${replayProgress.total})`
          : "Replay reference demo",
        hint: "five turns",
        icon: <Play className="size-3.5" />,
        disabled: newChatBusy,
        run: () => void replayReference(),
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
        disabled: newChatBusy,
        run: () => void submit({ utterance: question }),
      });
    }

    turns.forEach((turn, i) => {
      const label =
        turn.submission.utterance ??
        turn.submission.clarificationResponse ??
        untitledTurnLabel(turn.submission);
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
          scrollIntoViewRespectingMotion(
            document.getElementById(`referent-${entry.referent.value}`),
            { block: "center" },
          );
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
        id: "new-chat",
        group: "Workspace",
        label: "New chat",
        hint: driverKind === "api" ? "opens a fresh backend session" : "restarts the demo script",
        icon: <MessageSquarePlus className="size-3.5" />,
        disabled: newChatBusy,
        run: () => void newChat(),
      },
      {
        id: "settings",
        group: "Workspace",
        label: "Settings",
        hint: debug ? "internal · debug on" : "internal",
        icon: <Settings2 className="size-3.5" />,
        run: openSettings,
      },
      {
        id: "theme",
        group: "Workspace",
        label: `Switch to ${resolvedTheme === "dark" ? "light" : "dark"} theme`,
        icon: <SunMoon className="size-3.5" />,
        run: () => setTheme(resolvedTheme === "dark" ? "light" : "dark"),
      },
      {
        // Under judgement, so the row says which layout is on screen and
        // which one Enter moves to — a toggle that only names its
        // destination makes a reviewer guess what they have been reading.
        id: "answer-variant",
        group: "Workspace",
        label: `Answer layout: ${ANSWER_VARIANT_LABELS[variant]} → ${
          ANSWER_VARIANT_LABELS[nextAnswerVariant(variant)]
        }`,
        hint: ANSWER_VARIANT_HINTS[nextAnswerVariant(variant)],
        icon: <LayoutTemplate className="size-3.5" />,
        run: () => setAnswerVariant(nextAnswerVariant(variant)),
      },
      {
        id: "reset",
        group: "Workspace",
        label: "Reset session",
        hint: "same as New chat",
        icon: <RotateCcw className="size-3.5" />,
        disabled: newChatBusy,
        run: () => void newChat(),
      },
    );
    // The mock driver is a dev/test fixture, not a casual toggle — this
    // action only exists when the env itself already forces mock, i.e. a
    // dev build where flipping to the live API for a spot-check is a
    // reasonable ⌘K action. On the default (live API) env it disappears.
    if (envForcedMock) {
      list.push({
        id: "driver",
        group: "Workspace",
        label: `Switch to ${driverKind === "api" ? "mock" : "live API"} driver`,
        hint: "reloads the page",
        icon: <Repeat className="size-3.5" />,
        run: () => {
          window.localStorage.setItem("revi-driver", driverKind === "api" ? "mock" : "api");
          window.location.reload();
        },
      });
    }
    return list;
  }, [
    nextQuestion,
    newChatBusy,
    replayProgress,
    replayReference,
    submit,
    turns,
    referents,
    openDrawer,
    resolvedTheme,
    setTheme,
    newChat,
    openSettings,
    debug,
    variant,
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
              // The selection lives on a row two elements away and is
              // moved with the arrow keys while focus stays here, so the
              // state has to be ANNOUNCED as well as drawn.
              role="combobox"
              aria-expanded
              aria-controls="palette-list"
              aria-autocomplete="list"
              aria-activedescendant={
                filtered[clampedSelected] && !filtered[clampedSelected].disabled
                  ? `palette-option-${clampedSelected}`
                  : undefined
              }
              className="h-11 w-full bg-transparent text-body outline-none placeholder:text-muted-foreground"
            />
            <kbd className="rounded border bg-surface-sunken px-1.5 py-0.5 font-mono text-micro text-muted-foreground">
              esc
            </kbd>
          </div>
          <div
            ref={listRef}
            id="palette-list"
            role="listbox"
            aria-label="Commands"
            className="max-h-[19rem] overflow-y-auto p-1.5"
          >
            {filtered.length === 0 && (
              <p className="px-3 py-6 text-center text-meta text-muted-foreground">
                Nothing matches “{query}”.
              </p>
            )}
            {filtered.map((action, i) => {
              const showGroup = action.group !== lastGroup;
              lastGroup = action.group;
              return (
                <Fragment key={action.id}>
                  {showGroup && (
                    <p
                      role="presentation"
                      className="px-2.5 pb-1 pt-2.5 text-micro font-semibold uppercase tracking-[0.14em] text-muted-foreground"
                    >
                      {action.group}
                    </p>
                  )}
                  <button
                    type="button"
                    data-index={i}
                    id={`palette-option-${i}`}
                    role="option"
                    aria-selected={i === clampedSelected && !action.disabled}
                    disabled={action.disabled}
                    onMouseEnter={() => setSelected(i)}
                    onClick={() => runAction(action)}
                    className={cn(
                      // The row Enter will fire needs a real indicator, not
                      // a tint: `bg-accent` measures 1.05:1 against the
                      // overlay in dark and 1.19:1 in light, and the hover
                      // variant 1.06:1 — so "selected" and "hovered" were
                      // the same pixel on a menu holding "Reset session"
                      // and "New chat", both of which discard an open
                      // investigation with no undo. The 2px `--ring` rail
                      // is 9.34:1 dark / 3.74:1 light against that same
                      // overlay. Every row reserves the 2px so arrowing
                      // never shifts the labels.
                      "flex w-full items-center gap-2.5 rounded-md border-l-2 border-l-transparent px-2.5 py-2 text-left text-body transition-colors duration-150",
                      i === clampedSelected && !action.disabled
                        ? "border-l-ring bg-accent font-medium text-foreground"
                        : "text-secondary-foreground",
                      // Disabled content is exempt from the contrast
                      // floor, and these rows earn no benefit from the
                      // exemption: they are the ones a user reads to find
                      // out why the palette went inert mid-turn. At 40%
                      // `--secondary-foreground` measured 2.36:1 light /
                      // 2.96:1 dark on the overlay; at 65% it is 4.76:1 /
                      // 5.66:1 and still visibly a step down.
                      action.disabled && "opacity-65",
                    )}
                  >
                    <span className="text-muted-foreground">{action.icon}</span>
                    <span className="min-w-0 flex-1 truncate">{action.label}</span>
                    {/* An inert row says WHY it is inert. Dimming alone
                        put the answer entirely in a colour difference —
                        and these are the rows a user most needs to read,
                        because they are the ones that did not respond. */}
                    {(action.disabled ? busyReason : action.hint) && (
                      <span className="shrink-0 text-micro text-muted-foreground">
                        {action.disabled ? busyReason : action.hint}
                      </span>
                    )}
                  </button>
                </Fragment>
              );
            })}
          </div>
          <div className="flex items-center gap-3 border-t px-3.5 py-2 text-micro text-muted-foreground">
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
