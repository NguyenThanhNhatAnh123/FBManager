import assert from 'node:assert/strict';
import {canPublishPage} from '../frontend/posting-policy.js';

assert.equal(canPublishPage({can_publish:true,information_status:'updated'}),true);
assert.equal(canPublishPage({can_publish:false,information_status:'updated'}),false,'The common policy overrides an old information label');
for(const value of [1,'true',null,undefined])assert.equal(canPublishPage({can_publish:value,information_status:'updated'}),false,'Malformed policy flags fail closed');
assert.equal(canPublishPage({information_status:'updated'}),true,'Older servers retain their accepted-information marker');
assert.equal(canPublishPage({information_status:'not_updated'}),false);
assert.equal(canPublishPage({}),false);
console.log('PASS posting selector policy: authoritative exact booleans, closed malformed flags and explicit legacy marker.');
