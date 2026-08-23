import { Link } from "react-router-dom";
import type { Company } from "../../types/models";

interface Props {
  companies: Company[];
  onRemove: (companyId: string) => void;
}

export function TickerTable({ companies, onRemove }: Props) {
  if (companies.length === 0) {
    return (
      <div className="empty-state">
        No tickers yet — add one above to start ingesting filings for it.
      </div>
    );
  }

  return (
    <table className="ticker-table">
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Name</th>
          <th>Sector</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {companies.map((company) => (
          <tr key={company.id}>
            <td className="mono">
              <Link to={`/companies/${company.ticker}`}>{company.ticker}</Link>
            </td>
            <td>{company.name}</td>
            <td>{company.sector ?? "—"}</td>
            <td>
              <button className="btn" onClick={() => onRemove(company.id)}>
                Remove
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
