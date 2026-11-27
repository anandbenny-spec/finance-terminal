export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs" && process.env.CALLS_BUILD_ID) {
    const { installReadTrace } = await import("./lib/calls-delivery/trace");
    installReadTrace();
  }
}
