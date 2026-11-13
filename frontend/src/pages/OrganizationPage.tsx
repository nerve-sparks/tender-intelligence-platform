import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import OfferingProfileEditor from "@/components/companies/OfferingProfileEditor";
import { useToastSimple } from "@/components/ui/toaster-simple";
import { getCompanyId } from "@/lib/auth";
import { deleteCompanyDocument, fetchCompanyProfile, processCompanyProfile } from "@/services/tenderAgentApi";

const OrganizationPage = () => {
  const companyId = useMemo(() => getCompanyId(), []);
  const { pushToast } = useToastSimple();
  const queryClient = useQueryClient();

  const { data: company } = useQuery({
    queryKey: ["company", companyId],
    queryFn: () => fetchCompanyProfile(companyId || ""),
    enabled: Boolean(companyId),
  });

  const reprocessMutation = useMutation({
    mutationFn: () => processCompanyProfile(companyId || ""),
    onSuccess: () => pushToast("Reprocessing started", "success"),
    onError: () => pushToast("Unable to trigger reprocess", "error"),
  });

  const removeMutation = useMutation({
    mutationFn: (fileHash: string) => deleteCompanyDocument(companyId || "", fileHash),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["company", companyId] });
    },
    onError: () => pushToast("Unable to remove file", "error"),
  });

  return (
    <div className="space-y-4">
      <h1 className="text-3xl font-semibold text-[#232937] md:text-4xl">My Organization</h1>

      <div className="rounded-xl border border-[#d8dce6] bg-white p-5">
        <h2 className="text-xl font-semibold text-[#2b3040] md:text-2xl">Company Info</h2>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <div>
            <p className="text-xs text-[#7c849a]">Name</p>
            <p className="text-base text-[#232937]">{company?.name || "-"}</p>
          </div>
          <div>
            <p className="text-xs text-[#7c849a]">Company URL</p>
            <p className="text-base text-[#232937]">{company?.company_url || "-"}</p>
          </div>
          <div>
            <p className="text-xs text-[#7c849a]">Experience</p>
            <p className="text-base text-[#232937]">{company?.experience_years || 0} years</p>
          </div>
          <div>
            <p className="text-xs text-[#7c849a]">Status</p>
            <p className="text-base text-[#232937]">{company?.status?.processing_status || "pending"}</p>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          {(company?.interest_tags || []).map((tag) => (
            <span key={tag} className="rounded-full border border-[#cfd3df] bg-[#f5f6fa] px-3 py-1 text-xs text-[#4f5770]">
              {tag}
            </span>
          ))}
        </div>

        <button
          onClick={() => reprocessMutation.mutate()}
          className="mt-4 rounded-full bg-[#4040E0] px-5 py-2 text-sm text-white"
        >
          Reprocess Profile
        </button>
      </div>

      <OfferingProfileEditor company={company} />

      <div className="rounded-xl border border-[#d8dce6] bg-white p-5">
        <h2 className="text-xl font-semibold text-[#2b3040] md:text-2xl">Uploaded Documents</h2>
        <div className="mt-3 space-y-2">
          {(company?.uploaded_files || []).map((file) => (
            <div key={file.file_hash} className="flex items-center justify-between rounded border border-[#e5e8ef] p-3">
              <div>
                <p className="text-sm text-[#232937]">{file.original_name}</p>
                <p className="text-xs text-[#7a8195]">{file.extract_status || "stored_only"}</p>
              </div>
              <button
                onClick={() => removeMutation.mutate(file.file_hash)}
                className="rounded border border-[#ef8f93] px-3 py-1 text-xs text-[#dd5056]"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-xl border border-[#d8dce6] bg-white p-5">
        <h2 className="text-xl font-semibold text-[#2b3040] md:text-2xl">Website Scrape Preview</h2>
        <p className="mt-2 max-h-52 overflow-y-auto whitespace-pre-wrap rounded border border-[#e5e8ef] bg-[#f7f8fb] p-3 text-sm text-[#4c546a]">
          {company?.website_scrape?.extracted_text || "No scraped content available."}
        </p>
      </div>
    </div>
  );
};

export default OrganizationPage;
