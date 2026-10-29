// /: the front page (#152; Design 01 §1.17; Brief §10), the first version of "Start here". It leads with What changed
// at the latest FOMC and MPC, then the votes, ChangeStrip and where rates stand (components/policy/FrontView.tsx).
// Every figure is a row an `api` view returned with the browser key (lib/queries/front.ts), formatted by
// lib/format.ts. It replaces the temporary / → /policy redirect (#86).
import { FrontView } from "@/components/policy/FrontView";
import { readFront } from "@/lib/queries/front";

// The title, description and preview card are the root layout's: "Finance Terminal" and the one-line positioning (lib/site.ts).
export default async function HomePage() {
  return <FrontView front={await readFront()} />;
}
