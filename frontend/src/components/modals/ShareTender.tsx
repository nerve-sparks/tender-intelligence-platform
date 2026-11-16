import { useMemo, useState } from "react";
import { X } from "lucide-react";

import { shareTenderByEmail } from "@/services/tenderAgentApi";
import { useToastSimple } from "@/components/ui/toaster-simple";

interface ShareTenderProps {
  open: boolean;
  onClose: () => void;
  tenderId?: string;
  companyId: string;
  /** Dialog heading; defaults to the single-tender wording. */
  title?: string;
  /** Text on the button while sending. */
  sendingLabel?: string;
  /**
   * Custom send (e.g. all saved tenders at once). Return the toast message on success.
   * When omitted, the single tender `tenderId` is shared.
   */
  onSend?: (recipients: string[]) => Promise<string>;
}

interface Recipient {
  email: string;
  name: string;
}

const ShareTender = ({
  open,
  onClose,
  tenderId = "",
  companyId,
  title = "Share Tender by Email",
  sendingLabel = "Sending...",
  onSend,
}: ShareTenderProps) => {
  const [input, setInput] = useState("");
  const [recipients, setRecipients] = useState<Recipient[]>([]);
  const [sending, setSending] = useState(false);
  const { pushToast } = useToastSimple();

  const canDone = useMemo(() => recipients.length > 0 && !sending, [recipients.length, sending]);

  const addRecipient = () => {
    const email = input.trim().toLowerCase();
    if (!email) return;
    if (!/^\S+@\S+\.\S+$/.test(email)) {
      pushToast("Enter a valid email", "error");
      return;
    }
    if (recipients.some((item) => item.email === email)) {
      setInput("");
      return;
    }

    const namePart = email.split("@")[0];
    const name = namePart
      .split(/[._-]/)
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");

    setRecipients((prev) => [...prev, { email, name: name || "User" }]);
    setInput("");
  };

  const removeRecipient = (email: string) => {
    setRecipients((prev) => prev.filter((item) => item.email !== email));
  };

  const submit = async () => {
    if (!canDone) return;
    setSending(true);
    if (onSend) {
      try {
        const message = await onSend(recipients.map((item) => item.email));
        pushToast(message, "success");
        setRecipients([]);
        onClose();
      } catch (error: any) {
        pushToast(error?.response?.data?.error?.message || error?.message || "Unable to send email", "error");
      } finally {
        setSending(false);
      }
      return;
    }
    try {
      const result = await shareTenderByEmail(tenderId, {
        company_id: companyId,
        recipients: recipients.map((item) => item.email),
      });
      if (result.sent) {
        pushToast(`Shared with ${result.count} recipient(s)`, "success");
        onClose();
      } else {
        pushToast("Unable to share tender", "error");
      }
    } catch {
      pushToast("Unable to share tender", "error");
    } finally {
      setSending(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/55 p-4">
      <div className="w-full max-w-[820px] rounded-md bg-[#f7f7f7] p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-xl font-semibold text-[#171b23] md:text-2xl">{title}</h3>
          <button onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className="mt-3 flex gap-2">
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Enter Email..."
            className="h-10 flex-1 rounded border border-[#c9ceda] bg-white px-3 text-sm"
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addRecipient();
              }
            }}
          />
          <button onClick={addRecipient} className="h-10 rounded bg-[#4040E0] px-5 text-sm text-white">
            Add
          </button>
        </div>

        <div className="mt-3 rounded border border-[#c9ceda] bg-white p-3">
          <p className="mb-2 text-xs text-[#1f242f]">Individuals authorized to receive the tender</p>
          <div className="flex min-h-[150px] flex-wrap gap-3">
            {recipients.map((recipient) => (
              <div
                key={recipient.email}
                className="flex w-[220px] items-center justify-between rounded-md border border-[#e2e5ec] bg-[#f8f8f8] px-2 py-2"
              >
                <div className="flex items-center gap-2">
                  <div className="flex h-8 w-8 items-center justify-center rounded bg-[#ece0f3] text-xs font-semibold text-[#a46cc6]">
                    {recipient.name
                      .split(" ")
                      .slice(0, 2)
                      .map((part) => part[0])
                      .join("")
                      .toUpperCase()}
                  </div>
                  <div>
                    <p className="text-sm font-medium text-[#1f242f]">{recipient.name}</p>
                    <p className="text-[11px] text-[#7d8598]">{recipient.email}</p>
                  </div>
                </div>
                <button onClick={() => removeRecipient(recipient.email)}>
                  <X size={15} />
                </button>
              </div>
            ))}
          </div>
        </div>

        <button
          disabled={!canDone}
          onClick={submit}
          className="mx-auto mt-3 block h-11 min-w-[160px] rounded-2xl bg-[#4040E0] px-7 text-sm text-white disabled:opacity-50"
        >
          {sending ? sendingLabel : "Done"}
        </button>
      </div>
    </div>
  );
};

export default ShareTender;
