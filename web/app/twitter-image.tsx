// The Twitter card image, the same card (#86; Brief §10). Generated at build time from the tokens; no figures.
import { CARD_ALT, CARD_SIZE, CARD_TYPE, renderCard } from "@/lib/og/card";

export const alt = CARD_ALT;
export const size = CARD_SIZE;
export const contentType = CARD_TYPE;

export default async function Image() {
  return renderCard();
}
