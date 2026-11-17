// The command bar's one server read (K-07, #585; Spec 07 §3, N1): whether `site.command_bar` is published. Its own file, so the
// shell's reads (lib/queries/shell.ts) are untouched; the same shape as readTourShown, with the same cache tags.
import { cacheLife, cacheTag } from "next/cache";
import { commandBarShown } from "@/lib/cmdbar";
import { failed } from "@/lib/queries/overview";
import { readFieldPolicy } from "@/lib/queries/policy";
import { publicClient } from "@/lib/supabase/public";

/**
 * Whether the command bar is published: `site.command_bar` in api.field_policy with status shown or labelled. No row (the state
 * until K-11's judge passes and Anand flips), a failed read or no key all mean hidden (fails closed): the top bar is unchanged.
 */
export async function readCommandBarShown(): Promise<boolean> {
  "use cache";
  cacheLife("minutesNoExpire");
  cacheTag("shell", "field-policy");
  const db = publicClient();
  if (!db) return false;
  try {
    return commandBarShown(await readFieldPolicy(db));
  } catch (error) {
    cacheLife("failedRead");
    failed("command bar field policy", error);
    return false;
  }
}
