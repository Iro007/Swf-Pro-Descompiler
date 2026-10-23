'use strict';
const { v2 } = require('./v2');
const { legacy } = require('./legacy');
const { suite } = require('./suite');

const ADAPTERS = Object.freeze({ v2, legacy, suite });

function adapterFor(framing) {
  const adapter = ADAPTERS[framing];
  if (!adapter) throw new Error(`Sin adaptador para el framing ${framing}`);
  return adapter;
}

module.exports = { ADAPTERS, adapterFor };
