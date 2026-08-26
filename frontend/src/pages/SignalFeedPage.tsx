import { useState } from "react";
import { SignalTable } from "../components/signals/SignalTable";
import { SignalFilterBar } from "../components/signals/SignalFilterBar";
import { useSignals } from "../hooks/useSignals";
import type { SignalType } from "../types/models";

export function SignalFeedPage() {
  const [signalType, setSignalType] = useState<SignalType | "">("");
  const [minConfidence, setMinConfidence] = useState(0);
  // Defaults to on: an ever-growing undifferentiated list defeats the point
  // of a feed. Reviewed items are still one checkbox away.
  const [unreviewedOnly, setUnreviewedOnly] = useState(true);

  const { data: signals, isLoading, isError } = useSignals({
    signal_type: signalType || undefined,
    min_confidence: minConfidence || undefined,
    unreviewed_only: unreviewedOnly,
  });

  return (
    <div>
      <div className="page-title-row">
        <h2>Signal feed</h2>
        <span className="faint" style={{ fontSize: 10 }}>
          {signals ? `${signals.length} SHOWN` : ""}
        </span>
      </div>

      <SignalFilterBar
        signalType={signalType}
        onSignalType={setSignalType}
        minConfidence={minConfidence}
        onMinConfidence={setMinConfidence}
        unreviewedOnly={unreviewedOnly}
        onUnreviewedOnly={setUnreviewedOnly}
      />

      {isError && <p className="error-text">Can't reach the Loom API. Confirm the backend is running and reload.</p>}

      <div className="panel">
        {isLoading ? (
          <p className="empty-state">Loading…</p>
        ) : (
          <SignalTable
            signals={signals ?? []}
            emptyMessage={
              unreviewedOnly
                ? 'Nothing unreviewed. Uncheck "unreviewed only" to see everything.'
                : 'No signals yet. Open a company and choose "analyse filings", or add a new ticker.'
            }
          />
        )}
      </div>
    </div>
  );
}
