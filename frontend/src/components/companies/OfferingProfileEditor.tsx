import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { useToastSimple } from "@/components/ui/toaster-simple";
import { updateCompanyOffering } from "@/services/tenderAgentApi";
import { CompanyProfile } from "@/types/tender-agent";

const LIST_FIELDS: Array<{ key: string; label: string; hint: string }> = [
  { key: "core_capabilities", label: "What you deliver", hint: "Specific capabilities, e.g. LLM fine-tuning, road construction" },
  { key: "offerings", label: "Products / services you sell", hint: "Named offerings" },
  { key: "target_sectors", label: "Sectors you serve", hint: "e.g. defence, healthcare, manufacturing" },
  { key: "excluded_capabilities", label: "What you do NOT do", hint: "Tenders requiring these are rejected, e.g. hardware supply" },
  {
    key: "tender_search_keywords",
    label: "GeM search keywords",
    hint: "Short terms searched on GeM every scrape, e.g. artificial intelligence, software development",
  },
];

const PROCUREMENT_TYPES = [
  { value: "goods", label: "Goods (supply)" },
  { value: "services", label: "Services" },
  { value: "software", label: "Software / IT" },
  { value: "consulting", label: "Consulting" },
  { value: "works", label: "Works (construction / installation)" },
];

const toText = (value: unknown) => (Array.isArray(value) ? value.join(", ") : "");
const toList = (value: string) =>
  value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

/**
 * The offering profile every tender is matched against. Extracted from the uploaded
 * documents, editable by the company. Saving re-runs relevance matching.
 */
const OfferingProfileEditor = ({ company }: { company?: CompanyProfile }) => {
  const { pushToast } = useToastSimple();
  const queryClient = useQueryClient();
  const meta = (company?.metadata || {}) as Record<string, unknown>;

  const [fields, setFields] = useState<Record<string, string>>({});
  const [types, setTypes] = useState<string[]>([]);
  const [turnover, setTurnover] = useState("");
  const [experience, setExperience] = useState("");
  const [isStartup, setIsStartup] = useState(false);
  const [isMse, setIsMse] = useState(false);

  useEffect(() => {
    const next: Record<string, string> = {};
    LIST_FIELDS.forEach((field) => {
      next[field.key] = toText(meta[field.key]);
    });
    setFields(next);
    setTypes(Array.isArray(meta.preferred_procurement_types) ? (meta.preferred_procurement_types as string[]) : []);
    setTurnover(meta.annual_turnover_inr ? String(Number(meta.annual_turnover_inr) / 1e7) : "");
    setExperience(company?.experience_years !== undefined ? String(company.experience_years) : "");
    setIsStartup(meta.is_dpiit_startup === true);
    setIsMse(meta.is_mse === true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company?.company_id, company?.metadata]);

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload: Record<string, unknown> = { preferred_procurement_types: types };
      LIST_FIELDS.forEach((field) => {
        payload[field.key] = toList(fields[field.key] || "");
      });
      const crores = parseFloat(turnover);
      if (!Number.isNaN(crores) && crores > 0) payload.annual_turnover_inr = crores * 1e7;
      const years = parseInt(experience, 10);
      if (!Number.isNaN(years) && years >= 0 && years <= 100) payload.experience_years = years;
      payload.is_dpiit_startup = isStartup;
      payload.is_mse = isMse;
      return updateCompanyOffering(company?.company_id || "", payload);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["company", company?.company_id] });
      queryClient.invalidateQueries({ queryKey: ["matches", company?.company_id] });
      queryClient.invalidateQueries({ queryKey: ["tenderMatch", company?.company_id] });
      pushToast("Offering saved. Tender matches are being recalculated.", "success");
    },
    onError: () => pushToast("Unable to save offering profile", "error"),
  });

  if (!company) return null;
  const learned = Array.isArray(meta.learned_search_keywords) ? (meta.learned_search_keywords as string[]) : [];

  return (
    <div className="rounded-xl border border-[#d8dce6] bg-white p-5">
      <h2 className="text-xl font-semibold text-[#2b3040] md:text-2xl">Offering Profile</h2>
      <p className="mt-1 text-sm text-[#7a8195]">
        Tenders are matched against this profile. It was extracted from your documents - correct anything that is wrong.
        Separate entries with commas.
      </p>

      {typeof meta.business_type === "string" && meta.business_type && (
        <p className="mt-3 text-sm text-[#232937]">
          <span className="text-[#7c849a]">Business type: </span>
          {meta.business_type}
        </p>
      )}

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        {LIST_FIELDS.map((field) => (
          <label key={field.key} className="block">
            <span className="text-xs font-medium text-[#4f5770]">{field.label}</span>
            <textarea
              value={fields[field.key] || ""}
              onChange={(event) => setFields((prev) => ({ ...prev, [field.key]: event.target.value }))}
              placeholder={field.hint}
              rows={3}
              className="mt-1 w-full rounded border border-[#d5d9e5] p-2 text-sm text-[#232937]"
            />
          </label>
        ))}
      </div>

      <div className="mt-4">
        <span className="text-xs font-medium text-[#4f5770]">Contract types you can fulfil</span>
        <div className="mt-2 flex flex-wrap gap-2">
          {PROCUREMENT_TYPES.map((type) => {
            const active = types.includes(type.value);
            return (
              <button
                key={type.value}
                type="button"
                onClick={() =>
                  setTypes((prev) => (active ? prev.filter((t) => t !== type.value) : [...prev, type.value]))
                }
                className={`rounded-full border px-3 py-1 text-xs ${
                  active ? "border-[#4040E0] bg-[#4040E0] text-white" : "border-[#cfd3df] text-[#4f5770]"
                }`}
              >
                {type.label}
              </button>
            );
          })}
        </div>
      </div>

      {learned.length > 0 && (
        <div className="mt-4">
          <span className="text-xs font-medium text-[#4f5770]">Learned from your best matches (also searched on GeM)</span>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {learned.map((keyword) => (
              <span key={keyword} className="rounded-full bg-[#eef0ff] px-2 py-0.5 text-xs text-[#4040E0]">
                {keyword}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="mt-4 grid max-w-xl gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="text-xs font-medium text-[#4f5770]">Average annual turnover (₹ crore)</span>
          <input
            value={turnover}
            onChange={(event) => setTurnover(event.target.value)}
            inputMode="decimal"
            className="mt-1 w-full rounded border border-[#d5d9e5] p-2 text-sm text-[#232937]"
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-[#4f5770]">Years of experience</span>
          <input
            value={experience}
            onChange={(event) => setExperience(event.target.value)}
            inputMode="numeric"
            className="mt-1 w-full rounded border border-[#d5d9e5] p-2 text-sm text-[#232937]"
          />
        </label>
      </div>

      <div className="mt-4 space-y-2">
        <span className="text-xs font-medium text-[#4f5770]">GeM eligibility relaxations</span>
        <label className="flex items-center gap-2 text-sm text-[#232937]">
          <input type="checkbox" checked={isStartup} onChange={(event) => setIsStartup(event.target.checked)} />
          DPIIT-recognised startup (Startup India)
        </label>
        <label className="flex items-center gap-2 text-sm text-[#232937]">
          <input type="checkbox" checked={isMse} onChange={(event) => setIsMse(event.target.checked)} />
          Registered Micro / Small Enterprise (MSME / Udyam)
        </label>
        <p className="text-xs text-[#7a8195]">
          When a GeM bid offers Startup or MSE relaxation, the matching experience / turnover criteria are waived.
        </p>
      </div>

      <button
        onClick={() => saveMutation.mutate()}
        disabled={saveMutation.isPending}
        className="mt-4 rounded-full bg-[#4040E0] px-5 py-2 text-sm text-white disabled:opacity-60"
      >
        {saveMutation.isPending ? "Saving..." : "Save & re-match tenders"}
      </button>
    </div>
  );
};

export default OfferingProfileEditor;
