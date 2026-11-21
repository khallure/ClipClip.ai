'use strict';

// This application uses dynamic API routes and prebuilt static UI assets. Runtime
// ISR/fetch entries are disposable; keeping none avoids writes into the package.
module.exports = class DesktopCache {
  async get() { return null; }
  async set() {}
  async revalidateTag() {}
  resetRequestCache() {}
};
