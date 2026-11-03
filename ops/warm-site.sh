#!/usr/bin/env bash
# Loads the live site's main pages a few times after a successful production run (#263; Plan 04 §3.1, gate (a)).
#
# Why: a page's cached copy refreshes only when someone asks for it. The first request after a run is served the copy
# built before the run (and re-builds it in the background), so the first visitor would see yesterday's data and the
# second today's. Loading each page here, a few seconds apart, makes that first visitor this script: the round after
# the first one gets the rebuilt copy, so a real visitor gets the data the run just stored. next.config.ts keeps a
# copy from expiring in a quiet spell (an expired copy is replaced by the last deploy's build output), so the warmed
# copy is still the one served hours later.
#
# It reads public pages only: no key, no secret, no query string (the cache ignores one). It never fails the run: the
# data is already stored, so a site that can't be reached is a warning. Each load prints its status, Vercel's cache
# state and the copy's age, which is the record of the two-loads check (the second round should read HIT).
#
# Settings (environment): SITE_URL (default: the shared link), WARM_ROUNDS (3), WARM_GAP_SECONDS (20).
set -u

site="${SITE_URL:-https://finance-terminal-phi.vercel.app}"
site="${site%/}"
rounds="${WARM_ROUNDS:-3}"
gap="${WARM_GAP_SECONDS:-20}"
paths=(/ /policy /accuracy /screener)

case "$rounds$gap" in
  ''|*[!0-9]*)
    echo "::warning title=warm-site::WARM_ROUNDS and WARM_GAP_SECONDS must be whole numbers: nothing was loaded"
    exit 0
    ;;
esac

headers="$(mktemp)"
trap 'rm -f "$headers"' EXIT
bad=0
for round in $(seq 1 "$rounds"); do
  bad=0
  for path in "${paths[@]}"; do
    code=$(curl -sS -o /dev/null -D "$headers" -m 30 -w '%{http_code}' "${site}${path}" 2>/dev/null) || code=000
    cache=$(tr -d '\r' < "$headers" | sed -n 's/^[Xx]-[Vv]ercel-[Cc]ache: *//p' | head -n 1)
    age=$(tr -d '\r' < "$headers" | sed -n 's/^[Aa]ge: *//p' | head -n 1)
    echo "round ${round} ${path} ${code} x-vercel-cache=${cache:-none} age=${age:-none}"
    [ "$code" = "200" ] || bad=$((bad + 1))
  done
  if [ "$round" -lt "$rounds" ]; then
    sleep "$gap"
  fi
done

if [ "$bad" -gt 0 ]; then
  echo "::warning title=warm-site::${bad} of ${#paths[@]} pages did not answer 200 in the last round; the data is stored, but the site may show the old copy to its next visitor"
fi
exit 0
