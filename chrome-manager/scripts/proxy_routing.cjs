'use strict';
// Domain routing only. Never inspect browser tabs, account data or TLS payloads.
const FACEBOOK_DOMAINS=Object.freeze([
 'facebook.com','facebook.net','fbcdn.net','fbsbx.com','messenger.com',
 'fb.com','fb.me','fb.watch','accountscenter.meta.com','auth.meta.com','account.meta.com'
]);
function usesUpstream(hostname,mode='all'){
 if(!['all','facebook_only'].includes(mode))throw new Error('Invalid routing mode');
 const host=String(hostname).toLowerCase().replace(/\.+$/,'');
 return mode==='all'||FACEBOOK_DOMAINS.some(domain=>host===domain||host.endsWith('.'+domain));
}
module.exports={FACEBOOK_DOMAINS,usesUpstream};
