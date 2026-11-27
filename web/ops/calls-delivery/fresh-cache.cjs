// Bypass every "use cache" entry during preparation, including metadata and
// the shell. A retry has no path to reuse a prior successful or failed read.
module.exports = {
  async get() { return undefined; },
  async set(_key, pending) {
    const entry = await pending;
    const stream = entry.value.getReader();
    while (!(await stream.read()).done) { /* drain the cache stream */ }
  },
  async getExpiration() { return 0; },
  async refreshTags() {},
  async updateTags() {},
};
