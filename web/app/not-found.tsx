// Not found, inside the shell (C2), instead of Next's default page (white, unstyled).
import Link from "next/link";

export default function NotFound() {
  return (
    <>
      <h1 className="text-page">Page not found</h1>
      <p className="mt-1 text-ink-2">
        There&apos;s no page at this address.{" "}
        <Link href="/" className="text-ink-1 underline">
          Go to Finance Terminal
        </Link>
      </p>
    </>
  );
}
