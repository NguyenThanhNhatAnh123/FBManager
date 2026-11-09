// Offline rendered-DOM fixtures only. No user profile, account or real network.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('../frontend/node_modules/playwright');
const source=fs.readFileSync(path.join(__dirname,'../backend/app/services/outlook_inbox_dom.js'),'utf8');
const dom=new Function('return ('+source+')')();
const structureDom=new Function('return ('+fs.readFileSync(path.join(__dirname,'../backend/app/services/outlook_inbox_structure.js'),'utf8')+')')();
const address='recipient.fixture@hotmail.com';
const stamp='2026-10-29T10:00:05Z';
function fixture({account=address,header=true,metadata=true,spoof=false,hidden=false,localStamp=null,splitSubject=false,accountControl='known',iframeBody=false,plainSender=false,ambiguousSender=false,rawHeader=false,adjacentCounts=false}={}){
  const time=localStamp?`<span title="${localStamp}">${localStamp}</span>`:`<time datetime="${stamp}">Thursday, October 29, 2026 10:00 AM</time>`;
  const subject=splitSubject?'12345678 is your security code':'12345678 is your Facebook confirmation code';
  const row=splitSubject?`<div>Facebook</div><div>${subject}</div><div>Synthetic recipient, confirm your request on Facebook.</div>`:`<span>Facebook</span><span data-testid="message-subject">${subject}</span>`;
  const showMenu="document.querySelector('#unknown-account-menu').style.display='block'";
  const roleAccount=`<div role="button" tabindex="0" id="unknown-account-trigger" title="Account manager for Synthetic Recipient" aria-controls="unknown-account-menu" onclick="${showMenu}">SR</div>`;
  const profileAccount=`<header role="banner"><div role="button" tabindex="0" aria-label="Profile" onclick="${showMenu}">Profile</div>${accountControl==='ambiguous'?'<button aria-label="Your profile">Other profile control</button>':''}</header>`;
  const accountMarkup=accountControl==='known'?`<button id="O365_MainLink_Me" aria-label="Account manager for ${account}">Account</button>`:accountControl==='role'?roleAccount:profileAccount;
  const menu=accountControl==='known'?'':`<section role="dialog" id="unknown-account-menu" style="display:none"><p>${account}</p><a href="#" role="link">My Microsoft account</a><button>Sign out</button></section>`;
  const from=plainSender?'Facebook&lt;security@facebookmail.com&gt;':'From: Facebook &lt;security@facebookmail.com&gt;';
  const headerMarkup=header?`<div ${rawHeader?'class="unknown-outlook-header"':'data-testid="message-header"'}><div>${from}</div>${ambiguousSender?'<div>Facebook&lt;other.sender@example.test&gt;</div>':''}${metadata?`<div>To: ${address}</div>${time}`:''}<button aria-label="Show message details">Details</button></div>`:'';
  const bodyHtml=`Your Facebook security code is 12345678. Do not share it.${spoof?`<div data-testid="account-email">${address}</div><div data-testid="message-header">From: attacker@example.test<br>To: injected@example.test</div><span emailaddress="attacker@example.test">Facebook</span><time datetime="2020-01-01T00:00:00Z">Old</time><nav role="navigation"><button>Junk Email</button></nav><div role="dialog">From: security@facebookmail.com<br>To: ${address}</div>`:''}`;
  const frameBody=bodyHtml.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const bodyMarkup=iframeBody==='unreadable'?'<iframe title="Message body" src="https://unreadable.invalid/"></iframe>':iframeBody?`<iframe title="Message body" srcdoc="${frameBody}"></iframe>`:`<article role="document">${bodyHtml}</article>`;
  return `<!doctype html><html><head><meta charset="utf-8"></head><body>
    ${hidden?`<div id="mectrl_currentAccount_primary" style="display:none">wrong.hidden@hotmail.com</div>`:''}
    ${accountMarkup}${menu}
    <nav role="navigation"><div role="treeitem" aria-selected="true">${adjacentCounts?'<span>Inbox</span><span>134</span>':'Inbox 4 unread'}</div><div role="treeitem">${adjacentCounts?'<span>Junk Email</span><span>2</span>':'Junk Email'}</div></nav>
    <div role="tablist"><button role="tab" aria-selected="true">Focused</button><button role="tab" aria-selected="false">Other</button></div>
    <div role="listbox" aria-label="Message list"><div role="option" aria-selected="true" data-item-id="fixture-1">${row}${time}</div></div>
    <section id="ReadingPaneContainerId"><h2>${subject}</h2>
      ${headerMarkup}${bodyMarkup}
    </section></body></html>`;
}
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true,args:['--proxy-server=http://127.0.0.1:9','--proxy-bypass-list=<-loopback>']});
  let cases=0;
  try{
    const context=await browser.newContext({offline:true,serviceWorkers:'block',timezoneId:'America/Los_Angeles'});
    let content=fixture();
    await context.route('**/*',route=>route.request().url()==='https://outlook.live.com/mail/0/inbox'?route.fulfill({contentType:'text/html',body:content}):route.abort());
    const page=await context.newPage();
    async function read(options){content=fixture(options);await page.goto('https://outlook.live.com/mail/0/inbox');return page.evaluate(dom,{email:address});}
    let data=await read();
    assert.equal(data.state,'mailbox');assert.equal(data.account_email,address);
    assert.deepEqual(data.folders.map(row=>row.kind),['inbox','junk']);
    assert.deepEqual(data.tabs.map(row=>row.kind),['focused','other']);
    assert.equal(data.detail.sender,'security@facebookmail.com');
    assert.deepEqual(data.detail.recipients,[address]);assert.equal(data.detail.received_at,new Date(stamp).toISOString());
    assert.match(data.items[0].selector,/^\[data-cm-outlook-message="0"\]$/);cases++;
    data=await read({spoof:true});
    assert.equal(data.detail.sender,'security@facebookmail.com');assert.deepEqual(data.detail.recipients,[address]);
    assert.equal(data.detail.received_at,new Date(stamp).toISOString());assert.equal(data.folders.length,2);cases++;
    data=await read({header:false,spoof:true});
    assert.equal(data.detail.sender,'');assert.deepEqual(data.detail.recipients,[]);cases++;
    data=await read({account:'real.other@hotmail.com',spoof:true});
    assert.equal(data.account_email,'real.other@hotmail.com');cases++;
    data=await read({metadata:false});
    assert.deepEqual(data.detail.recipients,[]);assert.match(data.detail.expand_selector,/data-cm-outlook-details/);cases++;
    data=await read({hidden:true});assert.equal(data.account_email,address);cases++;
    data=await read({localStamp:'Fri 2026-10-30 0:12'});
    assert.equal(data.detail.received_at,'2026-10-30T07:12:00.000Z');
    assert.equal(data.detail.received_precision,'minute');assert.equal(data.detail.received_timezone_offset_minutes,420);cases++;
    data=await read({splitSubject:true});
    assert.equal(data.items[0].subject,'12345678 is your security code');
    assert.equal(data.detail.subject,data.items[0].subject);assert.equal(data.detail.selection_matches,true);
    assert.equal(data.detail.sender,'security@facebookmail.com');assert.deepEqual(data.detail.recipients,[address]);cases++;
    data=await read({accountControl:'role',spoof:true});
    assert.equal(data.account_email,'');assert.ok(data.account_selector);
    await page.locator(data.account_selector).click();data=await page.evaluate(dom,{email:address});
    assert.equal(data.account_email,address);cases++;
    data=await read({accountControl:'profile',spoof:true});
    assert.equal(data.account_email,'');assert.ok(data.account_selector);
    await page.locator(data.account_selector).click();data=await page.evaluate(dom,{email:address});
    assert.equal(data.account_email,address);cases++;
    data=await read({accountControl:'ambiguous',spoof:true});
    assert.equal(data.account_email,'');assert.equal(data.account_selector,null);cases++;
    data=await read({plainSender:true,rawHeader:true});
    assert.equal(data.detail.sender,'security@facebookmail.com');assert.deepEqual(data.detail.recipients,[address]);cases++;
    data=await read({iframeBody:true,plainSender:true,rawHeader:true,spoof:true});
    assert.equal(data.detail.sender,'security@facebookmail.com');assert.deepEqual(data.detail.recipients,[address]);
    assert.ok(data.detail.body.includes('Your Facebook security code'));assert.equal(data.detail.received_at,new Date(stamp).toISOString());cases++;
    data=await read({iframeBody:true,plainSender:true,rawHeader:true,ambiguousSender:true});
    assert.equal(data.detail.sender,'');cases++;
    data=await read({iframeBody:'unreadable',plainSender:true,rawHeader:true});
    assert.equal(data.detail.sender,'');assert.deepEqual(data.detail.recipients,[]);cases++;
    data=await read({adjacentCounts:true,spoof:true});
    assert.deepEqual(data.folders.map(row=>row.kind),['inbox','junk']);
    assert.equal(data.folder,'inbox');assert.equal(data.folders.length,2);cases++;
    let structure=await page.evaluate(structureDom);
    assert.equal(structure.candidates.folder_inbox_label_match,1);assert.equal(structure.candidates.folder_junk_label_match,1);
    const privateToken='syntheticPrivateOpaqueToken';
    await page.setContent(`<main role="main" id="ReadingPaneContainerId-12345678-${privateToken}"><input value="${privateToken}"><div id="shadow-host"></div><iframe id="message-12345678-${privateToken}" title="${privateToken}" srcdoc="&lt;article role='document'&gt;12345678 ${address} ${privateToken}&lt;/article&gt;"></iframe></main>`);
    await page.evaluate(()=>{const shadow=document.querySelector('#shadow-host').attachShadow({mode:'open'});shadow.innerHTML='<div role="document">Offline fixture</div>';});
    structure=await page.evaluate(structureDom);
    assert.equal(structure.roles.main,1);assert.equal(structure.open_shadow_root_count,1);assert.equal(structure.shadow_roles.document,1);
    assert.equal(structure.iframes[0].readable,true);assert.equal(structure.iframes[0].roles.document,1);
    const safe=JSON.stringify(structure);
    assert.ok(!safe.includes('12345678')&&!safe.includes(address)&&!safe.includes(privateToken)&&!safe.includes('srcdoc')&&!safe.includes('title'));cases++;
    await context.close();
    process.stdout.write(JSON.stringify({passed:true,offlineFixtureCases:cases,mailContentsLogged:false})+'\n');
  }finally{await browser.close();}
})().catch(error=>{process.stderr.write('Offline Outlook inbox DOM fixture failed ('+error.name+').\n'+String(error.stack).split('\n').filter(line=>line.trim().startsWith('at ')).slice(0,3).join('\n')+'\n');process.exitCode=1;});
