const callsPath = /^\/companies\/([a-z0-9-]+)\/calls(?:\/([a-z0-9-]{1,40}))?$/;

export function defaultPath(scope) {
  return scope === "@earnings" ? "/earnings" : `/companies/${scope}/calls`;
}

export function preparedRequest(pathname, headers) {
  const segmentUrl = /^(.*)\.segments(\/.*)\.segment\.rsc$/.exec(pathname);
  const rscUrl = pathname.endsWith(".rsc");
  const path = segmentUrl?.[1] ?? (rscUrl ? pathname.slice(0, -4) : pathname);
  const match = callsPath.exec(path);
  const scope = match?.[1] ?? (path === "/earnings" ? "@earnings" : null);
  if (!scope) return null;
  const rsc = rscUrl || headers.get("rsc") === "1";
  const segment = segmentUrl?.[2] ?? (rsc ? headers.get("next-router-segment-prefetch") : null);
  return {
    path,
    scope,
    quarter: match?.[2],
    variant: segment ? `segment:${segment === "/" ? "/_index" : segment}` : rsc ? "rsc" : "html",
  };
}
