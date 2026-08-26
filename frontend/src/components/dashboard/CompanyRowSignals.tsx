import { SignalTable } from "../signals/SignalTable";
import { useSignals } from "../../hooks/useSignals";

interface Props {
  ticker: string;
  colSpan: number;
}

const PREVIEW_LIMIT = 6;

/** Mounted only while its row is expanded (see DashboardPage). Reuses the
 *  existing /signals endpoint and the same grid the feed uses, so a company's
 *  top findings read identically wherever they appear and cost no new backend
 *  surface. */
export function CompanyRowSignals({ ticker, colSpan }: Props) {
  const { data: signals, isLoading } = useSignals({ ticker, limit: PREVIEW_LIMIT });

  return (
    <tr className="detail-row">
      <td colSpan={colSpan} style={{ padding: "0 0 0 22px" }}>
        {isLoading ? (
          <p className="empty-state">loading…</p>
        ) : (
          <SignalTable
            signals={signals ?? []}
            showTicker={false}
            emptyMessage="No signals for this company yet."
          />
        )}
      </td>
    </tr>
  );
}
