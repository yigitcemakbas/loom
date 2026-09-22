import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useDashboard } from "../../hooks/useDashboard";

/** Find a company by ticker or name.
 *
 *  Loom tracked a hundred and thirty companies and the only way to reach one
 *  was to click a link that happened to be on screen. That is workable while
 *  the product is a set of pages you read and useless the moment it is a tool
 *  you use, because the first thing anyone does with a research tool is look
 *  up the company they are already thinking about.
 *
 *  Filtered in the browser rather than through an endpoint. The whole universe
 *  is a hundred and thirty rows that the dashboard query already holds, so a
 *  round trip per keystroke would add latency to buy nothing. This stops being
 *  true at a few thousand companies, which is the point to move it server
 *  side. */
export function CompanySearch() {
  const navigate = useNavigate();
  const { data } = useDashboard();
  const [query, setQuery] = useState("");
  const [highlighted, setHighlighted] = useState(0);
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const companies = data?.companies ?? [];

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    const scored = companies
      .map((c) => {
        const ticker = c.ticker.toLowerCase();
        const name = c.name.toLowerCase();
        // An exact ticker beats a prefix beats anything inside a name. Someone
        // typing "V" wants Visa, not every company with a v in its name.
        if (ticker === q) return { c, rank: 0 };
        if (ticker.startsWith(q)) return { c, rank: 1 };
        if (name.startsWith(q)) return { c, rank: 2 };
        if (name.includes(q)) return { c, rank: 3 };
        return null;
      })
      .filter((m): m is { c: (typeof companies)[number]; rank: number } => m !== null);
    scored.sort((a, b) => a.rank - b.rank || a.c.ticker.localeCompare(b.c.ticker));
    return scored.slice(0, 8).map((m) => m.c);
  }, [query, companies]);

  useEffect(() => setHighlighted(0), [query]);

  // Slash focuses the box from anywhere, the convention in every tool built
  // for people who keep their hands on the keyboard.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing = target && /^(INPUT|TEXTAREA)$/.test(target.tagName);
      if (event.key === "/" && !typing) {
        event.preventDefault();
        inputRef.current?.focus();
      }
      if (event.key === "Escape") {
        setOpen(false);
        inputRef.current?.blur();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    function onClick(event: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) setOpen(false);
    }
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, []);

  function go(ticker: string) {
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
    navigate(`/companies/${ticker}`);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!matches.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlighted((h) => (h + 1) % matches.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlighted((h) => (h - 1 + matches.length) % matches.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(matches[highlighted].ticker);
    }
  }

  return (
    <div className="company-search" ref={boxRef}>
      <input
        ref={inputRef}
        className="search-input"
        value={query}
        placeholder="Find a company  /"
        onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        aria-label="Find a company by ticker or name"
      />
      {open && query.trim() !== "" && (
        <ul className="search-results">
          {matches.length === 0 && (
            <li className="search-empty">
              Nothing matching “{query.trim()}”. Loom tracks {companies.length} companies.
            </li>
          )}
          {matches.map((c, i) => (
            <li
              key={c.ticker}
              className={i === highlighted ? "highlighted" : ""}
              onMouseEnter={() => setHighlighted(i)}
              onMouseDown={(e) => { e.preventDefault(); go(c.ticker); }}
            >
              <span className="search-ticker">{c.ticker}</span>
              <span className="search-name">{c.name}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
