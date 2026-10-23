'use strict';
/**
 * Workers that already speak protocol v2 (RustSWF): nothing to translate. Their
 * answers still pass through the common normalizers so the application sees the
 * same field names whichever engine produced them.
 */
const { scriptItem, page } = require('./normalize');

const v2 = {
  name: 'v2',
  async invoke(op, args, ctx) {
    const reply = await ctx.rpc(op, args);
    if (op === 'scripts' && reply && Array.isArray(reply.items)) {
      return page(reply, reply.items.map(scriptItem));
    }
    return reply;
  },
};

module.exports = { v2 };
