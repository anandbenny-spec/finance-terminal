// Only the private worker installs this Next incremental cache handler. A
// render is captured as one unit, including Flight and navigation segments.
class Capture {
  static entry;

  async get() {
    return null;
  }

  async set(key, value, context) {
    if (value?.kind === "APP_PAGE") Capture.entry = { key, value, cacheControl: context.cacheControl };
  }

  async revalidateTag() {}
  resetRequestCache() {}
}

module.exports = Capture;
