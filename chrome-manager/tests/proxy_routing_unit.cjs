'use strict';
const assert=require('node:assert/strict');
const {FACEBOOK_DOMAINS,usesUpstream}=require('../scripts/proxy_routing.cjs');
for(const domain of FACEBOOK_DOMAINS){
 assert.equal(usesUpstream(domain,'facebook_only'),true);
 assert.equal(usesUpstream('sub.'+domain,'facebook_only'),true);
 assert.equal(usesUpstream(domain.toUpperCase()+'.','facebook_only'),true);
 assert.equal(usesUpstream(domain+'.evil.invalid','facebook_only'),false);
 assert.equal(usesUpstream('evil'+domain,'facebook_only'),false);
}
for(const host of ['outlook.live.com','login.live.com','outlook.office.com','example.com','127.0.0.1','::1']){
 assert.equal(usesUpstream(host,'facebook_only'),false);
 assert.equal(usesUpstream(host,'all'),true);
}
assert.throws(()=>usesUpstream('facebook.com','invalid'));
console.log('PASS: anchored Facebook domain rules, subdomains, case/trailing dots, Outlook direct and legacy all-proxy mode.');
