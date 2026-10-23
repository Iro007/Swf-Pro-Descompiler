'use strict';
const path = require('node:path');
const { Harness, defaultRoots } = require('./harness');
const { Registry } = require('./registry');
const { Protocol } = require('./protocol');
const { HarnessError, KINDS } = require('./errors');
const scoring = require('./scoring');

/**
 * Harness for the running application: packaged builds read manifests and workers
 * from `resources`, development builds from the repository checkout.
 */
function createHarness(options = {}) {
  const env = options.env || process.env;
  const repoRoot = path.resolve(__dirname, '..', '..');
  const roots = options.roots || defaultRoots({ resourcesPath: options.resourcesPath, repoRoot: options.packaged ? null : repoRoot, env });
  return Harness.create({ ...options, env, roots });
}

module.exports = { createHarness, Harness, Registry, Protocol, HarnessError, KINDS, scoring, defaultRoots };
