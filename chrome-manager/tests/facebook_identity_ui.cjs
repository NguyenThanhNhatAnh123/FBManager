// Offline rendered-UI fixtures only. Never opens a real account or saved profile.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const identity=new Function('return ('+fs.readFileSync(path.join(root,'backend/app/services/facebook_identity_dom.js'),'utf8')+'\n)')();
const facebook=new Function('return ('+fs.readFileSync(path.join(root,'backend/app/services/facebook_dom.js'),'utf8')+'\n)')();
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true});
  const page=await context.newPage();
  await page.route('**/*',route=>route.fulfill({contentType:'text/html',body:'<main>Fixture</main>'}));
  await page.goto('https://www.facebook.com/profile.php?id=222');
  await page.setContent('<main><h1>Manage Page</h1><h1>Page Fixture</h1><button aria-label="Your profile">Account menu</button></main>');
  let data=await page.evaluate(identity);
  assert.equal(data.acting_page,true);assert.equal(data.current_name,'Page Fixture');
  assert.equal(data.current_url,'https://www.facebook.com/profile.php?id=222');
  assert.equal(data.controls.find(control=>control.kind==='menu').label,'Your profile');
  const account=await page.evaluate(facebook,{mode:'account'});
  assert.equal(account.is_page,true);assert.equal(account.name,'');
  await page.setContent('<main><h1>Page Fixture</h1></main><div role="menu"><button>See all profiles</button><a href="/profile.php?id=111"><img alt="">Account One</a><button>Log out</button><a href="https://example.com/NotAFacebookProfile"><img alt="">External destination</a></div>');
  data=await page.evaluate(identity);
  assert.equal(data.controls.filter(control=>control.kind==='picker').length,1);
  assert.equal(data.candidates.find(item=>item.name==='Account One').url,'https://www.facebook.com/profile.php?id=111');
  assert.equal(data.candidates.some(item=>item.name==='Log out'),false);
  assert.equal(data.candidates.some(item=>item.name==='External destination'),false,'External profile destinations must not be offered');
  await page.setViewportSize({width:375,height:720});
  await page.setContent('<main><h1>Menu</h1><button aria-label="Menu Facebook">Mobile menu</button><button aria-label="Account One, View your profile"><img alt="">Account One</button><button aria-label="Switch profile">Switch profile</button></main>');
  data=await page.evaluate(identity);
  assert.equal(data.controls.some(item=>item.kind==='menu'&&item.label==='Menu Facebook'),true,'Mobile Facebook menu remains supported in the mobile layout');
  assert.equal(data.controls.find(item=>item.kind==='picker').label,'Switch profile');
  assert.equal(data.candidates.find(item=>item.name==='Account One').url,'','Buttons without href must not inherit the current Page URL');
  await page.setViewportSize({width:1280,height:720});
  await page.setContent('<button aria-label="Menu Facebook">App grid</button><button aria-label="Votre profil">Actual account avatar</button><div role="dialog"><h1>Menu</h1><h2>Créer</h2><a href="/profile.php?id=222">London Luca</a><a href="/profile.php?id=222">London Luca Page link</a><button>Reel</button></div>');
  data=await page.evaluate(identity);
  assert.deepEqual(data.controls.filter(item=>item.kind==='menu').map(item=>item.label),['Votre profil'],'Desktop app grid must never be selected as the identity menu');
  assert.equal(data.candidates.length,0,'Desktop Menu cannot enable broad mobile main/body candidate discovery');
  await page.setContent('<button aria-label="Facebook menu">App grid only</button><div role="dialog"><h1>Menu</h1><a href="/profile.php?id=222">London Luca</a><a href="/profile.php?id=222">London Luca duplicate</a></div>');
  data=await page.evaluate(identity);assert.equal(data.ready,false,'Desktop app-grid shell alone is not a ready identity state');
  await page.setContent('<div role="menu"><h2>Menu</h2><button>See all profiles</button><a href="/profile.php?id=111"><img alt="">Account One</a></div>');
  data=await page.evaluate(identity);assert.equal(data.candidates.some(item=>item.name==='Account One'),true,'A real desktop profile picker with an explicit picker action remains supported');
  await page.setContent('<div role="dialog"><div role="radio" aria-checked="false">Account One\nPersonal profile</div><button aria-label="Recipient Page, switch into your page"><img alt="">Recipient Page</button><button disabled aria-label="Switch now">Switch now</button></div>');
  data=await page.evaluate(identity);
  assert.equal(data.candidates.find(item=>item.name==='Account One').kind,'personal');
  assert.equal(data.candidates.find(item=>item.name==='Recipient Page').kind,'page');
  assert.equal(data.controls.some(item=>item.kind==='switch_page'),false,'Disabled background Switch now is not an action target');
  await page.setContent('<main><h1>Personal Fixture</h1><div role="article"><h1>Manage Page</h1><h2>Confirm your identity</h2><button aria-label="Your profile">Advertisement</button></div><button style="display:none" aria-label="Switch now">Hidden</button></main>');
  data=await page.evaluate(identity);
  assert.equal(data.blocked,false);assert.equal(data.acting_page,false);assert.equal(data.controls.length,0);
  await page.setContent('<main><h1>Familien Spaß</h1><h2>Manage Page</h2><button id="real-account" aria-label="Your profile">Real account menu</button></main><div style="opacity:0"><div role="dialog"><button>Switch profiles</button><button>See all profiles</button><a href="/profile.php?id=999"><img alt="">Stale hidden account</a><input name="email" value="never-read"></div></div><div inert><button>Switch profile</button><h1>Stale inert identity</h1></div><div hidden><button>See all profiles</button></div><div style="visibility:hidden"><button style="visibility:visible">Switch account</button></div><div style="display:none"><button>Switch profiles</button></div>');
  data=await page.evaluate(identity);
  assert.equal(data.blocked,false,'Invisible stale login inputs cannot block identity verification');
  assert.equal(data.current_name,'Familien Spaß');assert.equal(data.acting_page,true);
  assert.deepEqual(data.controls.map(item=>item.kind),['menu'],'Opacity-zero, inert and hidden ancestor controls must not win over the real Your profile button');
  assert.equal(data.candidates.length,0,'Hidden stale identity rows must not become candidates');
  await page.locator('[data-cm-identity="'+data.controls[0].id+'"]').click();
  assert.equal(await page.locator('#real-account').getAttribute('data-cm-identity'),data.controls[0].id,'The retained marker identifies the live account control');
  await page.setContent('<span id="account-name">Your profile</span><button aria-labelledby="account-name" aria-label="Other label"><svg aria-hidden="true"></svg></button><div role="menu"><button aria-label="Other visible control">See all profiles</button></div>');
  data=await page.evaluate(identity);
  assert.equal(data.controls.find(item=>item.kind==='menu').label,'Your profile','Rendered aria-labelledby is the accessible name');
  assert.equal(data.controls.some(item=>item.kind==='picker'),true,'Known visible control text can match without inventing aliases for its accessible label');
  const guide='<h3>Go to Ads Manager</h3><p>to customise your ads with more detailed features and settings.</p>';
  await page.setContent('<main><h1>Manage Page</h1><h1>Familien Spaß</h1><button aria-label="Your profile">Account menu</button></main><div role="tooltip" id="page-guide"><button aria-label="Close" id="close-guide">×</button>'+guide+'<button id="advertise">Advertise</button></div>');
  await page.evaluate(()=>{window.fixtureActions=[];document.querySelector('#close-guide').onclick=()=>{window.fixtureActions.push('close');document.querySelector('#page-guide').remove()};document.querySelector('#advertise').onclick=()=>window.fixtureActions.push('advertise')});
  data=await page.evaluate(identity);
  const tip=data.controls.find(item=>item.kind==='dismiss_tip');assert.ok(tip,'Exact Ads Manager onboarding guide exposes its Close action');
  assert.equal(tip.label,'Close');
  await page.locator('[data-cm-identity="'+tip.id+'"]').click();
  assert.deepEqual(await page.evaluate(()=>window.fixtureActions),['close'],'Only Close is clicked, never Advertise');
  data=await page.evaluate(identity);assert.equal(data.controls.some(item=>item.kind==='dismiss_tip'),false);
  assert.equal(data.controls.some(item=>item.kind==='menu'),true);
  for(const unsafe of [
    '<div role="article"><button aria-label="Close">×</button>'+guide+'<button>Advertise</button></div>',
    '<div role="dialog"><h2>Check your email</h2><input placeholder="Code"><button aria-label="Close">×</button>'+guide+'<button>Advertise</button></div>',
    '<div role="dialog"><h2>Review Invitation</h2><button aria-label="Close">×</button>'+guide+'<button>Advertise</button></div>',
    '<div role="tooltip"><button aria-label="Close">×</button>'+guide+'</div>',
    '<div role="tooltip"><button aria-label="Close">×</button><button aria-label="Close">×</button>'+guide+'<button>Advertise</button></div>'
  ]){
    await page.setContent('<main><h1>Page Fixture</h1></main>'+unsafe);
    data=await page.evaluate(identity);assert.equal(data.controls.some(item=>item.kind==='dismiss_tip'),false,'Posts, code/invite modals, missing Advertise or ambiguous Close never expose automatic dismissal');
  }
  await page.setContent('<main><h1>Gérer la page</h1><h1>Page France</h1><button aria-label="Compte">Compte</button></main><div role="menu"><button>Voir tous les profils</button></div>');
  data=await page.evaluate(identity);
  assert.equal(data.acting_page,true);assert.equal(data.controls.some(item=>item.kind==='picker'),true);
  const frenchGuide='Accédez au Gestionnaire de publicités pour personnaliser vos publicités avec des fonctionnalités et des paramètres plus détaillés.';
  const sidebar='<aside style="width:336px"><h2>Gérer la Page</h2><a href="/profile.php?id=222"><img alt="">London Luca</a></aside>';
  await page.setContent(sidebar+'<main><div role="menu"><h2>Menu</h2><a href="/profile.php?id=222">Unrelated app-grid label</a><button>Créer</button><button>Reel</button></div></main><div role="tooltip" id="french-guide"><button aria-label="Fermer" id="fermer">×</button><p>'+frenchGuide+'</p></div>');
  await page.evaluate(()=>{window.fixtureCloseCount=0;document.querySelector('#fermer').onclick=()=>{window.fixtureCloseCount++;document.querySelector('#french-guide').remove()}});
  data=await page.evaluate(identity);
  assert.equal(data.acting_page,true);assert.equal(data.current_name,'London Luca','Exact managed-Page sidebar link supplies the missing main h1');
  assert.equal(data.ready,true,'A loaded bound sidebar is enough to recognize the current Page');
  const frenchTip=data.controls.find(item=>item.kind==='dismiss_tip');assert.ok(frenchTip,'Exact French guide with unique Fermer is supported without Advertise');
  assert.equal(frenchTip.label,'Fermer');await page.locator('[data-cm-identity="'+frenchTip.id+'"]').click();
  assert.equal(await page.evaluate(()=>window.fixtureCloseCount),1);
  await page.setContent('<aside style="width:336px"><h2>Gérer la Page</h2><a href="/profile.php?id=999">Wrong Page</a></aside><main><div role="menu"><a href="/profile.php?id=222">Do not infer from app grid</a></div></main>');
  data=await page.evaluate(identity);assert.equal(data.current_name,'','Sidebar must bind to the displayed Page URL; menu labels are not actor proof');
  for(const unsafe of [
    '<div role="article"><button aria-label="Fermer">×</button><p>'+frenchGuide+'</p></div>',
    '<div role="dialog"><h2>Review Invitation</h2><button aria-label="Fermer">×</button><p>'+frenchGuide+'</p></div>',
    '<div role="dialog"><h2>Vérifiez votre e-mail</h2><input placeholder="Code"><button aria-label="Fermer">×</button><p>'+frenchGuide+'</p></div>',
    '<div role="tooltip"><button aria-label="Fermer">×</button><p>'+frenchGuide+'</p><button>Delete Page</button></div>',
    '<div role="tooltip"><button aria-label="Fermer">×</button><p>Une autre fenêtre sans rapport.</p></div>'
  ]){
    await page.setContent(sidebar+unsafe);data=await page.evaluate(identity);
    assert.equal(data.controls.some(item=>item.kind==='dismiss_tip'),false,'French support never dismisses posts, invitations, code dialogs, unrelated or additional-action popups');
  }
  await page.setContent('<input name="email" value="fixture-private-email"><input name="pass" type="password" value="fixture-private-password">');
  data=await page.evaluate(identity);
  assert.deepEqual(data,{blocked:true});assert.equal(JSON.stringify(data).includes('fixture-private'),false);
  console.log('PASS: Page acting-identity detection, no Page-as-FB reading, desktop/mobile/French switch controls, exact profile links, disabled/external/post exclusion and no secret extraction. Offline fixtures only.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
