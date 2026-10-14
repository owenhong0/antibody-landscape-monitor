"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { apiPost } from "@/lib/api";

// The only client-side code on the page: POST from the browser, then ask Next to
// re-render the server components so the row, filters and nav badge all update.
export function MarkSeenButton({ id }: { id: number }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [refreshing, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  async function markSeen() {
    setBusy(true);
    setError(null);
    try {
      await apiPost(`/api/signals/${id}/seen`);
      startTransition(() => router.refresh());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={markSeen}
        disabled={busy || refreshing}
        className="whitespace-nowrap rounded-md border border-accent px-3 py-1 text-sm font-medium text-accent hover:bg-accent hover:text-on-accent disabled:opacity-50"
      >
        {busy || refreshing ? "Saving…" : "Mark seen"}
      </button>
      {error && <span className="text-xs text-sev-high">{error}</span>}
    </div>
  );
}