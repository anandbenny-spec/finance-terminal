export function defaultPath(scope: string): string;
export function preparedRequest(
  pathname: string,
  headers: Headers,
): { path: string; scope: string; quarter: string | undefined; variant: string } | null;
