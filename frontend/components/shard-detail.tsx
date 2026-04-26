"use client";

import * as React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X } from "@phosphor-icons/react/dist/ssr/X";
import { PushPin } from "@phosphor-icons/react/dist/ssr/PushPin";
import { Trash } from "@phosphor-icons/react/dist/ssr/Trash";
import { ArrowCounterClockwise } from "@phosphor-icons/react/dist/ssr/ArrowCounterClockwise";
import { TIER_LABEL, type MemoryShard } from "@/lib/types";
import { Kbd } from "@/components/hud";
import { cn } from "@/lib/utils";

/* -----------------------------------------------------------------------------
 * Shard detail — slide-in right rail.
 *
 * Shows the selected memory's content, metadata, and the two manual controls
 * (Pin, Prune). Prune is destructive: clicking it does NOT immediately
 * archive — instead it queues a 4-second toast with an Undo affordance.
 * Only after the timer elapses does the action commit. Matches the critique's
 * "no immediate-fire destructive button" guidance.
 * ---------------------------------------------------------------------------*/

const SPRING = { type: "spring", stiffness: 220, damping: 26 } as const;
const PRUNE_GRACE_MS = 4_000;

interface ShardDetailProps {
  shard: MemoryShard | null;
  pinned: Set<string>;
  onClose: () => void;
  onTogglePin: (id: string) => void;
  onConfirmPrune: (id: string) => void;
}

export function ShardDetail({
  shard,
  pinned,
  onClose,
  onTogglePin,
  onConfirmPrune,
}: ShardDetailProps) {
  const [pendingPrune, setPendingPrune] = React.useState<string | null>(null);
  const timerRef = React.useRef<number | null>(null);

  // Cancel any pending prune when the panel closes or selection changes.
  React.useEffect(() => {
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, []);

  React.useEffect(() => {
    setPendingPrune(null);
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, [shard?.id]);

  function startPrune(id: string) {
    setPendingPrune(id);
    timerRef.current = window.setTimeout(() => {
      onConfirmPrune(id);
      setPendingPrune(null);
      timerRef.current = null;
    }, PRUNE_GRACE_MS);
  }

  function cancelPrune() {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setPendingPrune(null);
  }

  return (
    <AnimatePresence>
      {shard && (
        <motion.aside
          initial={{ x: 24, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: 24, opacity: 0 }}
          transition={SPRING}
          className="fixed right-6 top-6 bottom-6 z-20
                     w-[min(380px,calc(100vw-3rem))]
                     border border-border bg-background
                     flex flex-col"
          aria-label="Memory detail"
        >
          <header className="flex items-center justify-between px-5 py-4 border-b border-border">
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] tracking-widest uppercase text-muted-foreground">
                Memory
              </span>
              <span className="font-mono text-xs text-muted-foreground/70">
                {shard.id.slice(0, 8)}…
              </span>
            </div>
            <button
              onClick={onClose}
              aria-label="Close memory detail"
              className="text-muted-foreground hover:text-foreground transition-colors p-1 -m-1"
            >
              <X size={14} weight="regular" />
            </button>
          </header>

          <div className="flex-1 overflow-y-auto p-5 flex flex-col gap-5">
            <p className="text-sm text-foreground/90 leading-relaxed">
              {shard.content}
            </p>

            <dl className="grid grid-cols-[auto_1fr] gap-x-5 gap-y-2 text-xs">
              <Field label="Tier">
                <span className="text-foreground">
                  {TIER_LABEL[shard.tier].name}
                </span>
                <span className="font-mono text-muted-foreground/70 ml-2">
                  {TIER_LABEL[shard.tier].abbrev}
                </span>
              </Field>
              <Field label="Utility">
                <span className="font-mono tabular-nums">
                  {shard.utility_score.toFixed(3)}
                </span>
              </Field>
              <Field label="Reads">
                <span className="font-mono tabular-nums">
                  {shard.access_count}
                </span>
              </Field>
              {shard.goal_context && (
                <Field label="Goal">
                  <span className="text-foreground/90">{shard.goal_context}</span>
                </Field>
              )}
              <Field label="Pinned">
                <span className={cn(
                  "font-mono",
                  pinned.has(shard.id) ? "text-accent" : "text-muted-foreground/70",
                )}>
                  {pinned.has(shard.id) ? "yes" : "no"}
                </span>
              </Field>
            </dl>
          </div>

          <footer className="border-t border-border">
            {pendingPrune === shard.id ? (
              <UndoBar
                ms={PRUNE_GRACE_MS}
                onCancel={cancelPrune}
              />
            ) : (
              <div className="flex items-stretch divide-x divide-border">
                <ActionButton
                  icon={<PushPin size={14} weight={pinned.has(shard.id) ? "fill" : "regular"} />}
                  label={pinned.has(shard.id) ? "Unpin" : "Pin"}
                  shortcut="P"
                  onClick={() => onTogglePin(shard.id)}
                  active={pinned.has(shard.id)}
                />
                <ActionButton
                  icon={<Trash size={14} weight="regular" />}
                  label="Prune"
                  shortcut="⌫"
                  onClick={() => startPrune(shard.id)}
                  destructive
                />
              </div>
            )}
          </footer>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground tracking-wide uppercase text-[10px] self-baseline">
        {label}
      </dt>
      <dd>{children}</dd>
    </>
  );
}

function ActionButton({
  icon,
  label,
  shortcut,
  onClick,
  active,
  destructive,
}: {
  icon: React.ReactNode;
  label: string;
  shortcut?: string;
  onClick: () => void;
  active?: boolean;
  destructive?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex-1 flex items-center justify-center gap-2 px-4 py-3 text-xs",
        "transition-colors active:translate-y-[1px]",
        active && "text-accent",
        destructive
          ? "text-muted-foreground hover:text-[var(--color-danger)]"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      <span>{label}</span>
      {shortcut && (
        <Kbd>{shortcut}</Kbd>
      )}
    </button>
  );
}

function UndoBar({ ms, onCancel }: { ms: number; onCancel: () => void }) {
  return (
    <div className="relative flex items-center justify-between px-5 py-3 text-xs overflow-hidden">
      <motion.div
        initial={{ scaleX: 0 }}
        animate={{ scaleX: 1 }}
        transition={{ duration: ms / 1000, ease: "linear" }}
        className="absolute inset-0 origin-left bg-[var(--color-danger)]/10"
        aria-hidden
      />
      <span className="relative text-foreground">
        Pruning in {(ms / 1000).toFixed(0)}s…
      </span>
      <button
        onClick={onCancel}
        className="relative flex items-center gap-1.5 text-accent hover:text-foreground transition-colors"
      >
        <ArrowCounterClockwise size={12} weight="regular" />
        Undo
      </button>
    </div>
  );
}
