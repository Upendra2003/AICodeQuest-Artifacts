// The Badges screen: every badge that exists, earned and locked, with
// progress toward the next one.
//
// A SEPARATE DESTINATION, not the chip strip it replaces. That strip
// showed only what you already had, in a row at the bottom of Home --
// which answers "what did I get" and never "what is next", and a ladder
// whose rungs are invisible is not a ladder. Locked cards carry their own
// threshold and a progress bar, so the next target is always on screen.
//
// The EARN ANIMATION lives here too (BadgeToast), but is mounted by
// whoever detects the earn, not by this screen -- a badge earned during a
// turn should be celebrated where the participant is standing, not the
// next time they happen to open this page.

import { useEffect, useRef, useState } from "react";

import { FaAward, FaCrown, FaGem, FaMedal, FaTrophy } from "./icons.js";
import { burstFrom } from "./confetti.js";
import { newlyEarned } from "./useBadges.js";

// Phase 42: the ladder runs past gold into platinum and legend, so a long
// bug-catching run keeps producing a visibly different reward rather than a
// third gold. Each tier has its own SHAPE as well as its own colour
// (ui.css) -- colour alone is not a distinction everyone can see.
const TIER_ICON = {
  bronze: FaMedal,
  silver: FaAward,
  gold: FaTrophy,
  platinum: FaGem,
  legend: FaCrown,
};
const TIER_ORDER = { bronze: 0, silver: 1, gold: 2, platinum: 3, legend: 4 };

function tierIcon(tier) {
  return TIER_ICON[tier] || FaMedal;
}

/**
 * One badge card. A locked card is deliberately still LEGIBLE -- greyed,
 * not hidden and not blurred. "There is something here you have not got"
 * is only motivating if you can read what it is.
 */
export function BadgeCard({ badge }) {
  const Icon = tierIcon(badge.tier);
  const pct = badge.progress === null ? null : Math.round(badge.progress * 100);
  return (
    <div
      className={`aq-badgecard aq-badgecard--${badge.tier}${badge.earned ? "" : " aq-badgecard--locked"}`}
      data-testid={`badge-${badge.id}`}
      data-earned={badge.earned ? "true" : "false"}
    >
      <span className="aq-badgecard__icon" aria-hidden="true">
        <Icon />
      </span>
      <span className="aq-badgecard__name">{badge.name}</span>
      <span className="aq-badgecard__tier">{badge.tier}</span>
      {badge.earned ? (
        <span className="aq-badgecard__state">Earned</span>
      ) : pct === null ? (
        <span className="aq-badgecard__state">Locked</span>
      ) : pct >= 100 ? (
        /* Threshold met, but the backend has not marked it earned yet.
           This is reachable, not hypothetical: the leaderboard aggregate
           refreshes on submit and on terminal transitions rather than on
           read, so it lags live metrics by a few turns (recorded in
           CLAUDE.md). Rendering "7 / 5" under a full bar beside the word
           Locked reads as a broken card; saying the award is pending
           reads as what it is. */
        <span className="aq-badgecard__state" data-testid={`badge-pending-${badge.id}`}>
          Earned — updating
        </span>
      ) : (
        <>
          <span className="aq-badgecard__bar" aria-hidden="true">
            <span className="aq-badgecard__fill" style={{ width: `${pct}%` }} />
          </span>
          {/* The raw numbers, not just the bar. A bar at 40% does not tell
              you whether you are two reviews away or thirty. */}
          <span className="aq-badgecard__state">
            {formatMetric(badge.current)} / {formatMetric(badge.value)}
          </span>
        </>
      )}
    </div>
  );
}

function formatMetric(value) {
  if (typeof value !== "number") return "—";
  // A rate badge (0.7) reads as 70%; a count badge (5) reads as 5.
  return value > 0 && value <= 1 ? `${Math.round(value * 100)}%` : String(Math.round(value));
}

/**
 * The celebration. Rotates in, holds, fades — and fires a confetti burst
 * from its own position.
 *
 * ONE AT A TIME, even when several are earned together: a stack of four
 * toasts is noise, and the queue means each is actually read. Auto-
 * dismissing rather than requiring a click, because a modal that
 * interrupts a turn to be dismissed is a tax dressed as a reward.
 */
export function BadgeToast({ badges, onDone, durationMs, confetti = true }) {
  const [index, setIndex] = useState(0);
  const ref = useRef(null);
  const badge = badges?.[index];
  // `gamification.toast_ms` when the caller has one (the milestone carries
  // it on the submit response); otherwise the pre-Phase-42 constant, so a
  // caller that does not know about the config still behaves.
  const hold = Number(durationMs) > 0 ? Number(durationMs) : 2600;

  useEffect(() => {
    if (!badge) return undefined;
    // `confetti` is the STUDY-level switch (gamification.confetti_enabled).
    // It is not the accessibility one -- confetti.js already no-ops under
    // prefers-reduced-motion, and that check must not be conditional on a
    // study setting.
    const cleanupConfetti = confetti && ref.current ? burstFrom(ref.current) : () => {};
    const timer = setTimeout(() => {
      if (index + 1 < badges.length) setIndex(index + 1);
      else onDone?.();
    }, hold);
    return () => {
      clearTimeout(timer);
      cleanupConfetti();
    };
  }, [badge, index, badges, onDone, hold, confetti]);

  if (!badge) return null;
  const Icon = tierIcon(badge.tier);
  return (
    <div
      ref={ref}
      className={`aq-badgetoast aq-badgetoast--${badge.tier}`}
      // The CSS animation runs for exactly as long as the timer above, so
      // the toast cannot fade out early and sit invisible, or still be on
      // screen after the game has moved on.
      style={{ "--aq-toast-ms": `${hold}ms` }}
      data-testid="badge-toast"
      data-tier={badge.tier}
      // A live region, so the celebration reaches a screen reader as an
      // announcement rather than being a purely visual event.
      role="status"
      aria-live="polite"
    >
      <span className="aq-badgetoast__icon" aria-hidden="true">
        <Icon />
      </span>
      <span className="aq-badgetoast__text">
        <strong>{badge.label || "Badge earned"}</strong>
        <span>{badge.name}</span>
      </span>
      {badges.length > 1 ? (
        <span className="aq-badgetoast__count">
          {index + 1} of {badges.length}
        </span>
      ) : null}
    </div>
  );
}

export default function BadgesView({ board }) {
  const rows = board || [];
  const earned = rows.filter((b) => b.earned);
  // Earned first, then by tier, so the next rung of a ladder sits beside
  // the one just cleared rather than at the far end of the grid.
  const ordered = [...rows].sort((a, b) => {
    if (a.earned !== b.earned) return a.earned ? -1 : 1;
    return (TIER_ORDER[a.tier] ?? 0) - (TIER_ORDER[b.tier] ?? 0);
  });

  return (
    <div data-testid="badges-view">
      <div className="aq-taskhead">
        <h2 className="aq-taskhead__title">Badges</h2>
        <span className="aq-scoreline__meta" data-testid="badge-earned-count">
          {earned.length} of {rows.length} earned
        </span>
      </div>
      <p className="aq-lede">
        Badges are awarded from your own recorded play. Locked ones show what
        they need, so there is always a next one.
      </p>
      {rows.length === 0 ? (
        <p className="aq-note" data-testid="badges-empty">
          No badges are configured.
        </p>
      ) : (
        <div className="aq-badgegrid">
          {ordered.map((badge) => (
            <BadgeCard key={badge.id} badge={badge} />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Watches an earned-badge list and celebrates anything new.
 *
 * Mounted once, high up, rather than per screen: a badge is earned by
 * something the participant just DID (rating a review, finishing a turn),
 * so the celebration has to find them where they are standing rather than
 * waiting until they next open the Badges page.
 *
 * The diff is asynchronous (chrome.storage), so a stale result from a
 * previous render must never fire — `cancelled` guards that, otherwise a
 * fast refresh can queue two toasts for the same badge.
 */
export function BadgeCelebration({ badges }) {
  const [queue, setQueue] = useState([]);

  useEffect(() => {
    if (!badges) return undefined; // not loaded yet -- nothing to diff against
    let cancelled = false;
    newlyEarned(badges).then((fresh) => {
      if (!cancelled && fresh.length) setQueue(fresh);
    });
    return () => {
      cancelled = true;
    };
  }, [badges]);

  if (!queue.length) return null;
  return <BadgeToast badges={queue} onDone={() => setQueue([])} />;
}
