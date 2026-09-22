import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchDigestSetting, setDigestSetting } from "../../api/digest";
import type { DigestFrequency } from "../../api/digest";

const OPTIONS: { value: DigestFrequency; label: string }[] = [
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "off", label: "Never" },
];

/** How often Loom may email you.
 *
 *  Placed on the portfolio page rather than in a settings screen, because the
 *  thing it emails about is what is on this page, and a preference buried two
 *  clicks from what it controls is a preference nobody finds.
 *
 *  Turning it off is one click with no confirmation step. A tool that makes
 *  leaving harder than arriving does not deserve the inbox. */
export function DigestSetting() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["digest"], queryFn: fetchDigestSetting });
  const save = useMutation({
    mutationFn: setDigestSetting,
    onSuccess: (next) => queryClient.setQueryData(["digest"], next),
  });

  if (!data) return null;

  return (
    <div className="digest-setting">
      <div className="digest-row">
        <span className="digest-label">Email me when something moves</span>
        <div className="horizon-row" style={{ margin: 0 }}>
          {OPTIONS.map((option) => (
            <button
              key={option.value}
              className={`horizon-btn ${data.frequency === option.value ? "active" : ""}`}
              onClick={() => save.mutate(option.value)}
              disabled={save.isPending}
              aria-pressed={data.frequency === option.value}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
      {/* Says what will actually happen, including when the answer is
          "nothing, because your address is unconfirmed". Somebody who sets a
          preference and receives nothing deserves to know which it was. */}
      <p className={`digest-message ${data.verified ? "" : "warn"}`}>{data.message}</p>
    </div>
  );
}
