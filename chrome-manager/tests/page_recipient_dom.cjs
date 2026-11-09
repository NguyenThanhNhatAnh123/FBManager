// Disposable offline pages only; no real account/profile is opened.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const source=new Function('return ('+fs.readFileSync(path.join(__dirname,'../backend/app/services/page_recipient_dom.js'),'utf8')+'\n)')();
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true}),page=await context.newPage();
  await page.route('**/*',route=>route.fulfill({body:'<main>Fixture</main>',contentType:'text/html'}));
  await page.goto('https://www.facebook.com/profile.php?id=555');
  await page.setContent('<h1>Page Fixture</h1><a href="/profile.php?id=555&notif_id=123">Sender invited you to manage Page Fixture\'s Page</a><a href="https://evil.test/Page">Sender invited you to manage External\'s Page</a>');
  let result=await page.evaluate(source,{mode:'invites'});
  assert.equal(result.invitations.length,1);assert.equal(result.invitations[0].name,'Page Fixture');
  await page.setContent('<h1>Page Fixture</h1><button>Review Invitation</button><input name="email" hidden>');
  result=await page.evaluate(source,{mode:'stage'});assert.equal(result.review,true);assert.equal(result.actions.review,'Review Invitation');assert.equal(result.blocked,false);
  for(const label of ['Review Invite','Xem lại lời mời','Xem lời mời','Xem xét lời mời']){
   await page.setContent('<div role="dialog"><button>'+label+'</button></div>');
   result=await page.evaluate(source,{mode:'stage'});assert.equal(result.review,true);assert.equal(result.actions.review,label);
  }
  await page.setContent('<button>Accept</button><div role="dialog"><h2>Review invite</h2><p>What you should know about partial control</p><button>Next</button></div>');
  result=await page.evaluate(source,{mode:'stage'});assert.equal(result.next,true);assert.equal(result.actions.next,'Next');assert.equal(result.accept,false);assert.equal(result.actions.accept,null);assert.equal(result.control,'partial');
  await page.setContent('<button>Review Invitation</button><div role="dialog" aria-hidden="true"><button>Review Invite</button></div><div role="dialog"><h2>Current dialog</h2><button>Next</button><button hidden>Accept</button></div>');
  result=await page.evaluate(source,{mode:'stage'});assert.equal(result.review,false);assert.equal(result.actions.review,null);assert.equal(result.actions.next,'Next');assert.equal(result.actions.accept,null);
  await page.setContent('<div role="dialog"><h2>Review invite</h2><p>partial control</p><button>Accept</button></div>');
  result=await page.evaluate(source,{mode:'stage'});assert.equal(result.accept,true);assert.equal(result.actions.accept,'Accept');
  await page.setContent('<button>Review Invitation</button><div role="dialog"><h2>Review Invitation</h2><p>What you should know about partial control:</p><button>Decline</button><button aria-label="Accept invitation">Accept</button></div><div role="dialog"><p>Unrelated background panel</p></div>');
  result=await page.evaluate(source,{mode:'stage'});assert.equal(result.title,'Review Invitation');assert.equal(result.accept,true);assert.equal(result.actions.accept,'Accept invitation');assert.equal(result.control,'partial');
  assert.equal(result.review,false,'Background Review Invitation is excluded from the active Accept dialog');
  assert.equal(await page.getByRole('button',{name:result.actions.accept,exact:true}).count(),1,'Returned action is the real accessible label');
  await page.setContent('<div role="dialog"><h2>Review Invitation</h2><span id="accept-label">Confirm the invitation</span><button aria-label="Accept invitation" aria-labelledby="accept-label">Accept</button></div>');
  result=await page.evaluate(source,{mode:'stage'});assert.equal(result.accept,true);assert.equal(result.actions.accept,'Confirm the invitation');assert.equal(await page.getByRole('button',{name:result.actions.accept,exact:true}).count(),1);
  await page.setContent('<div role="dialog"><h2>Review Invitation</h2><p>partial control</p><button aria-label="Accept invitation">Accept</button></div><div role="dialog"><h2>Unrelated dialog</h2><button>Next</button></div><div style="opacity:0"><div role="dialog"><h2>Check your email</h2><input aria-label="Code" value="87654321"><button>Continue</button></div></div>');
  result=await page.evaluate(source,{mode:'stage'});assert.equal(result.title,'Review Invitation');assert.equal(result.actions.accept,'Accept invitation');assert.equal(result.next,false);assert.equal(result.code,false);assert.equal(result.actions.continue,null);assert.ok(!JSON.stringify(result).includes('87654321'));
  await page.setContent('<div role="dialog"><h2>Check your email</h2><input aria-label="Code" value="12345678"><button aria-label="Confirm security code">Continue</button></div><div role="dialog"><p>Unrelated empty dialog</p></div>');
  result=await page.evaluate(source,{mode:'stage'});assert.equal(result.code,true);assert.equal(result.actions.continue,'Confirm security code');assert.ok(!JSON.stringify(result).includes('12345678'));
  await page.setContent('<div role="dialog"><h2>Check your email</h2><input aria-label="Code" value="12345678"><button>Continue</button></div>');
  result=await page.evaluate(source,{mode:'stage'});assert.equal(result.code,true);assert.equal(result.continue,true);assert.equal(result.actions.continue,'Continue');assert.ok(!JSON.stringify(result).includes('12345678'));
  await page.setContent('<div role="dialog"><h2>Check your email</h2><p>Enter the code we sent to your email</p><label for="generated-input">Code</label><input id="generated-input" autocomplete="off" value="87654321"><button disabled>Continue</button></div>');
  result=await page.evaluate(source,{mode:'stage'});assert.equal(result.code,true);assert.ok(!JSON.stringify(result).includes('87654321'));assert.equal(await page.getByRole('textbox',{name:'Code',exact:true}).count(),1);
  await page.setContent('<div role="dialog"><h2>Check your email</h2><input autocomplete="off"><button>Continue</button></div>');
  result=await page.evaluate(source,{mode:'stage'});assert.equal(result.code,true,'One text input in an explicit email-code dialog is recognizable');
  await page.setContent('<div role="dialog"><h2>Invite Accepted</h2><button>Switch now</button></div>');
  result=await page.evaluate(source,{mode:'stage'});assert.equal(result.accepted,true);assert.equal(result.switch_now,true);assert.equal(result.actions.switch_now,'Switch now');
  console.log('PASS: invitation filtering, Review Invitation/Invite and Vietnamese variants, literal review/next/accept/continue/switch labels, current-visible-dialog scope and masked code stage. Offline fixtures only.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
