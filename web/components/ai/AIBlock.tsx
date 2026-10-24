// AIBlock (Design 01 §1.10, §4.5): a sparkle icon and the block's kind ("AI summary", "What changed") with an info
// tooltip (the model and when it generated the text), then the bullets, each ending in its citation chips. Only
// published, cited outputs reach it (lib/policy/ai.ts drops any bullet without a verified citation).
import { Info, Sparkles } from "lucide-react";
import { Cited } from "@/components/ai/CitationChip";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatCheckedTime } from "@/lib/format";
import type { AiBlockData } from "@/lib/policy/ai";

export function AIBlock({ block, now, dense = true }: { block: AiBlockData; now: string; dense?: boolean }) {
  const about = `${block.model} · generated ${formatCheckedTime(block.createdAt, now)}`;
  return (
    <div>
      <div className="flex items-center gap-1.5 text-data font-medium text-ink-1">
        <Sparkles aria-hidden className="size-3.5 text-amber" />
        {block.label}
        <Tooltip>
          <TooltipTrigger asChild>
            <button type="button" className="grid size-6 place-items-center rounded-sm text-ink-3 hover:text-ink-1" aria-label={`About this AI text: ${about}`}>
              <Info aria-hidden className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent>{about}</TooltipContent>
        </Tooltip>
      </div>
      <ul className={`mt-1.5 list-disc space-y-1.5 pl-4.5 ${dense ? "text-ui" : "text-read"}`}>
        {block.bullets.map((b, i) => (
          <li key={i}>
            <Cited text={b.text} citations={b.citations} />
          </li>
        ))}
      </ul>
    </div>
  );
}
