import type { Candidate } from "@/lib/api";

/** "Leave 07:42, arrive 08:19" plus what it means for the deadline. Times are Toronto local time. */
export function timingLine(r: Candidate, deadlineNote?: string | null): string {
  const base = `Leave ${r.depart} · arrive ${r.arrive}`;
  if (deadlineNote) return `${base} · ${deadlineNote}`;
  if (r.meets_deadline === undefined) return base;
  const by = r.deadline_iso ? r.deadline_iso.slice(11, 16) : "your deadline";
  if (r.meets_deadline) return `${base} · makes your ${by} deadline${r.leave_by ? ` (leave by ${r.leave_by})` : ""}`;
  return r.earliest_arrival
    ? `${base} · can't arrive by ${by}: the earliest arrival is ${r.earliest_arrival}`
    : `${base} · misses your ${by} deadline${r.leave_by ? ` (leave by ${r.leave_by})` : ""}`;
}