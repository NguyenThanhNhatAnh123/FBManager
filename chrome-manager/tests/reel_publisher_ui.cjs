// Fresh offline Chrome fixtures only. No saved profile, account or live publish.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const dom=new Function('return ('+fs.readFileSync(path.join(__dirname,'../backend/app/services/reel_publisher_dom.js'),'utf8')+')')();
const target='https://www.facebook.com/profile.php?id=555',args={pageUrl:target,pageName:'Fixture Page',caption:'Fixture caption',pageActorVerified:true};
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true});const page=await context.newPage();
  await page.route('**/*',route=>route.fulfill({contentType:'text/html',body:'<main>Offline fixture</main>'}));
  await page.goto(target);
  await page.setContent('<main><h1>Fixture Page</h1><button>Create reel</button><article role="article"><button>Create reel</button><h2>Your reel has been published</h2></article><div style="opacity:0"><button>Create reel</button></div></main>');
  let data=await page.evaluate(dom,args);
  assert.equal(data.reel_context,false);assert.equal(data.buttons.filter(item=>item.kind==='create').length,1);
  assert.equal(data.published,false,'Post text cannot fabricate publication success');
  await page.setContent('<main><h1>Fixture Page</h1><a id="valid-create" href="https://www.facebook.com/reels/create/?surface=profile" aria-label="Create Reel">Reel</a><a href="/reels/">Reel</a><a href="/reels/?create=true">Create Reel</a><a href="https://facebook.com.evil.test/reels/create/">Create Reel</a><a href="https://user:pass@www.facebook.com/reels/create/">Create Reel</a><a href="/reels/create/">Unrelated destination</a><article role="article"><a href="/reels/create/">Reel</a></article></main>');
  data=await page.evaluate(dom,args);
  assert.equal(data.buttons.filter(item=>item.kind==='create').length,1,'Only known Create Reel text plus public Facebook composer href is actionable');
  const anchor=data.buttons.find(item=>item.kind==='create');assert.equal(anchor.label,'Create Reel');
  assert.equal(await page.locator('[data-cm-reel="'+anchor.id+'"]').getAttribute('id'),'valid-create');
  const menuFailures=[],verified={...args,pageActorVerified:true},unverified={...args,pageActorVerified:false};
  const pageMain='<main><h1>Fixture Page</h1><h2>Gérer la Page</h2><article role="article"><button>Reel</button><button>Reels</button><a href="/reels/">Reel</a></article></main>';
  const gridHeader='<header role="banner"><button id="app-grid" aria-label="Menu"><svg role="img"></svg></button></header>';
  const menuHtml=(entry='<button id="menu-reel">Reel</button>')=>'<aside id="fixture-app-menu"><h2>Menu</h2><div><h3>Social</h3><button id="social-reel">Reel</button><button>Reels</button></div><section id="fixture-create-column"><h3>Créer</h3>'+entry+'</section></aside>';
  async function menuCase(name,html,input,check){
    try{await page.setContent(html);const value=await page.evaluate(dom,input);await check(value)}
    catch(error){menuFailures.push({name,message:error.message})}
  }
  await menuCase('French app-grid outside main',gridHeader+pageMain+menuHtml(),verified,async value=>{
    assert.equal(value.reel_context,false);const create=value.buttons.filter(item=>item.kind==='create');assert.equal(create.length,1);
    assert.equal(await page.locator('[data-cm-reel="'+create[0].id+'"]').getAttribute('id'),'menu-reel');
    assert.equal(value.buttons.filter(item=>item.kind==='app_menu').length,0,'An already-open menu is not opened again');
  });
  await menuCase('French visible/accessibility mismatch',pageMain+menuHtml('<button id="menu-reel" aria-label="Créer un Reel pour votre Page">Reel</button>'),verified,async value=>{
    const create=value.buttons.filter(item=>item.kind==='create');assert.equal(create.length,1);assert.equal(create[0].label,'Créer un Reel pour votre Page');
    assert.equal(await page.getByRole('button',{name:create[0].label,exact:true}).count(),1);
  });
  await menuCase('Styled French title spans and production-sized icon markup',pageMain+'<aside><div><span>Menu</span></div><section><div><span>Créer</span></div><button id="dense-reel">Reel</button>'+('<span aria-hidden="true"></span>'.repeat(700))+'</section></aside>',verified,async value=>{
    const create=value.buttons.filter(item=>item.kind==='create');assert.equal(create.length,1);assert.equal(await page.locator('[data-cm-reel="'+create[0].id+'"]').getAttribute('id'),'dense-reel');
  });
  const deepMenuHtml=({titleDepth=0,columnTitleDepth=0,columnDepth=0,entry='<button id="deep-menu-reel">Reel</button>',social='<button id="deep-social-reel">Reel</button>'}={})=>{
    const wrap=(html,depth)=>'<div>'.repeat(depth)+html+'</div>'.repeat(depth);
    return '<aside id="deep-app-menu">'+wrap('<h2>Menu</h2>',titleDepth)+'<section><h3>Social</h3>'+social+'</section>'+wrap('<section id="deep-create-column">'+wrap('<h3>Créer</h3>',columnTitleDepth)+entry+'</section>',columnDepth)+'</aside>';
  };
  const expectDeepCreate=async value=>{
    assert.equal(value.reel_context,false);const create=value.buttons.filter(item=>item.kind==='create');assert.equal(create.length,1);
    assert.equal(await page.locator('[data-cm-reel="'+create[0].id+'"]').getAttribute('id'),'deep-menu-reel');
    assert.equal(value.buttons.some(item=>item.kind==='app_menu'),false,'Proven open menu does not trigger the app-grid again');
  };
  await menuCase('Menu title nested in twelve wrappers',pageMain+deepMenuHtml({titleDepth:12}),verified,expectDeepCreate);
  await menuCase('Create title nested in twelve wrappers before its sibling Reel entry',pageMain+deepMenuHtml({columnTitleDepth:12}),verified,expectDeepCreate);
  await menuCase('Create column nested twelve wrappers below the Menu common ancestor',pageMain+deepMenuHtml({columnDepth:12}),verified,expectDeepCreate);
  await menuCase('Menu and Create titles plus column each retain twelve wrapper ancestry',pageMain+deepMenuHtml({titleDepth:12,columnTitleDepth:12,columnDepth:12}),verified,expectDeepCreate);
  const semanticReel='<div id="deep-menu-reel" role="link" tabindex="0">Reel</div>';
  await menuCase('Focusable semantic Reel link belongs only to proven Create column',pageMain+deepMenuHtml({entry:semanticReel}),verified,async value=>{
    await expectDeepCreate(value);assert.equal(await page.locator('#deep-menu-reel').getAttribute('href'),null);assert.equal(await page.locator('#deep-menu-reel').getAttribute('role'),'link');
  });
  await menuCase('Deep Menu and Create ancestry supports semantic Reel link',pageMain+deepMenuHtml({titleDepth:12,columnTitleDepth:12,columnDepth:12,entry:semanticReel}),verified,expectDeepCreate);
  await menuCase('Social semantic Reel link cannot fill a different Create column',pageMain+deepMenuHtml({entry:'<button>Publication</button>',social:semanticReel}),verified,value=>{
    assert.equal(value.buttons.filter(item=>item.kind==='create').length,0);
  });
  await menuCase('Feed semantic Reel link inside Create remains excluded',pageMain+deepMenuHtml({entry:'<article role="article">'+semanticReel+'</article>'}),verified,value=>{
    assert.equal(value.buttons.filter(item=>item.kind==='create').length,0);
  });
  await menuCase('Unknown modal blocks background semantic Reel link',pageMain+deepMenuHtml({entry:semanticReel})+'<div role="dialog"><h2>Paramètres</h2><button>Fermer</button></div>',verified,value=>{
    assert.equal(value.buttons.filter(item=>['create','app_menu'].includes(item.kind)).length,0);
  });
  await menuCase('Unverified actor cannot use a semantic Reel link in Menu',pageMain+deepMenuHtml({entry:semanticReel}),unverified,value=>{
    assert.equal(value.buttons.filter(item=>['create','app_menu'].includes(item.kind)).length,0);
  });
  await menuCase('Semantic Reel link outside a proven Menu cannot be an entry',pageMain+'<aside><h2>Navigation</h2><section><h3>Créer</h3>'+semanticReel+'</section></aside>',verified,value=>{
    assert.equal(value.buttons.filter(item=>item.kind==='create').length,0);
  });
  await menuCase('Create column cannot borrow Social Reel',pageMain+'<aside><h2>Menu</h2><section><h3>Social</h3><button>Reel</button></section><section><h3>Créer</h3><button>Publication</button></section></aside>',verified,value=>assert.equal(value.buttons.filter(item=>item.kind==='create').length,0));
  await menuCase('Interactive Menu label is not an overlay title',pageMain+'<aside><button>Menu</button><section><span>Créer</span><button>Reel</button></section></aside>',verified,value=>assert.equal(value.buttons.filter(item=>item.kind==='create').length,0));
  await menuCase('Separate widgets cannot join through the Page application','<div>'+pageMain+'<aside><h2>Menu</h2></aside><section><h3>Créer</h3><button>Reel</button></section></div>',verified,value=>assert.equal(value.buttons.filter(item=>item.kind==='create').length,0));
  await menuCase('Nonmodal editor cannot become a menu ancestor',pageMain+'<aside><h2>Menu</h2><textarea aria-label="Post"></textarea><section><h3>Créer</h3><button>Reel</button></section></aside>',verified,value=>assert.equal(value.buttons.filter(item=>item.kind==='create').length,0));
  await menuCase('Menu dialog is the current recognized overlay',gridHeader+pageMain+'<div role="dialog"><h2>Menu</h2><section><h3>Créer</h3><button>Reel</button></section></div>',verified,value=>{
    assert.equal(value.buttons.filter(item=>item.kind==='create').length,1);assert.equal(value.reel_context,false);
  });
  await menuCase('Unrelated top dialog blocks a background menu',gridHeader+pageMain+menuHtml()+'<div role="dialog"><h2>Créer une publication</h2><div contenteditable="true" role="textbox">Fixture post</div><button>Partager</button></div>',verified,value=>{
    assert.equal(value.buttons.filter(item=>['create','app_menu','publish'].includes(item.kind)).length,0);
  });
  await menuCase('Unverified Page cannot use global menu',gridHeader+pageMain+menuHtml(),unverified,value=>{
    assert.equal(value.buttons.filter(item=>['create','app_menu'].includes(item.kind)).length,0);
  });
  await menuCase('No-main document cannot bypass unverified actor',gridHeader+menuHtml(),unverified,value=>{
    assert.equal(value.buttons.filter(item=>['create','app_menu'].includes(item.kind)).length,0);
  });
  await menuCase('No-main verified menu excludes Social Reel',gridHeader+menuHtml(),verified,async value=>{
    const create=value.buttons.filter(item=>item.kind==='create');assert.equal(create.length,1);assert.equal(await page.locator('[data-cm-reel="'+create[0].id+'"]').getAttribute('id'),'menu-reel');
  });
  await menuCase('Exact Create heading is required',pageMain+'<aside><h2>Menu</h2><section><h3>Découvrir</h3><button>Reel</button></section></aside>',verified,value=>{
    assert.equal(value.buttons.filter(item=>item.kind==='create').length,0);
  });
  await menuCase('Exact Menu heading is required',pageMain+'<aside><h2>Navigation</h2><section><h3>Créer</h3><button>Reel</button></section></aside>',verified,value=>{
    assert.equal(value.buttons.filter(item=>item.kind==='create').length,0);
  });
  for(const attribute of ['hidden','inert','aria-hidden="true"','style="opacity:0"'])await menuCase('Hidden menu '+attribute,pageMain+'<div '+attribute+'>'+menuHtml()+'</div>',verified,value=>{
    assert.equal(value.buttons.filter(item=>item.kind==='create').length,0);
  });
  await menuCase('Feed Menu/Create headings are excluded','<main><h1>Fixture Page</h1><article role="article">'+menuHtml()+'</article></main>',verified,value=>{
    assert.equal(value.buttons.filter(item=>['create','app_menu'].includes(item.kind)).length,0);
  });
  await menuCase('Create column duplicates remain explicit',pageMain+menuHtml('<button id="one">Reel</button><button id="two" aria-label="Créer un Reel">Reel</button>'),verified,value=>{
    const create=value.buttons.filter(item=>item.kind==='create'&&item.enabled);assert.equal(create.length,2,'Publisher _one must see ambiguity rather than a guessed first target');assert.notEqual(create[0].id,create[1].id);
  });
  await menuCase('Active Reel composer suppresses app menu entry',gridHeader+'<main><h1>Créer un Reel</h1><a href="/profile.php?id=555">Fixture Page</a><button>Suivant</button></main>'+menuHtml(),verified,value=>{
    assert.equal(value.reel_context,true);assert.equal(value.buttons.filter(item=>['create','app_menu'].includes(item.kind)).length,0);
  });
  await menuCase('Strict menu create anchors',pageMain+menuHtml('<a id="safe-menu-anchor" href="https://www.facebook.com/reels/create/?surface=menu" aria-label="Créer un Reel">Reel</a><a href="/reels/">Reel</a><a href="/reels/?create=true">Reel</a><a href="https://facebook.com.evil.test/reels/create/">Reel</a><a href="https://user:pass@www.facebook.com/reels/create/">Reel</a><a href="https://www.facebook.com:444/reels/create/">Reel</a>'),verified,async value=>{
    const create=value.buttons.filter(item=>item.kind==='create');assert.equal(create.length,1);assert.equal(await page.locator('[data-cm-reel="'+create[0].id+'"]').getAttribute('id'),'safe-menu-anchor');
  });
  await menuCase('App opener requires banner or header',gridHeader+pageMain+'<aside><button aria-label="Menu">Menu</button></aside>',verified,async value=>{
    const opener=value.buttons.filter(item=>item.kind==='app_menu'&&item.enabled);assert.equal(opener.length,1);assert.equal(await page.locator('[data-cm-reel="'+opener[0].id+'"]').getAttribute('id'),'app-grid');
  });
  await menuCase('App opener requires verified actor',gridHeader+pageMain,unverified,value=>{
    assert.equal(value.buttons.filter(item=>item.kind==='app_menu').length,0);
  });
  await menuCase('Regular-post modal cannot become an app menu',pageMain+'<div role="dialog"><h2>Créer une publication</h2><div contenteditable="true" role="textbox">Fixture post</div><div><h3>Menu</h3><section><h3>Créer</h3><button>Reel</button></section></div></div>',verified,value=>{
    assert.equal(value.buttons.filter(item=>['create','app_menu','publish'].includes(item.kind)).length,0);
  });
  await page.setContent('<main><h1>Create reel</h1><a href="/profile.php?id=555">Fixture Page</a><input type="file" accept="video/*" hidden><input type="file" accept="image/*"><textarea aria-label="Describe your reel">Fixture caption</textarea><button disabled>Publish</button><button>Next</button></main>');
  data=await page.evaluate(dom,args);
  assert.equal(data.reel_context,true);assert.equal(data.files.length,1,'Video uploader excludes unrelated image file inputs');
  assert.equal(data.captions.length,1);assert.equal(data.captions[0].matches,true);assert.equal(data.actor_matches,true);
  assert.equal(data.buttons.find(item=>item.kind==='publish').enabled,false,'Disabled Publish never becomes a click target');
  assert.equal(data.upload_ready,true);
  await page.setContent('<main><h1>Créer un Reel</h1><input type="file" accept="video/*" hidden><input type="file" accept="video/mp4" hidden><button id="actual-uploader">Importer une vidéo</button></main>');
  data=await page.evaluate(dom,args);
  assert.equal(data.file_input_count,2);assert.equal(data.use_upload_chooser,true);assert.equal(data.files.length,0,'Ambiguous hidden inputs use the single recognized native chooser instead');
  assert.equal(data.buttons.filter(item=>item.kind==='upload'&&item.enabled).length,1);
  await page.setContent('<main><h1>Créer un Reel</h1><input type="file" accept="video/*" hidden><input type="file" accept="video/mp4" hidden><button id="french-dropzone"><span>Ajouter une vidéo</span><span style="display:block">ou faire un glisser-déposer</span></button></main>');
  data=await page.evaluate(dom,args);assert.equal(data.use_upload_chooser,true);assert.equal(data.buttons.find(item=>item.kind==='upload').label,'Ajouter une vidéo ou faire un glisser-déposer');
  for(const controls of ['', '<button>Importer une vidéo</button><button>Ajouter une vidéo</button>', '<button disabled>Importer une vidéo</button>', '<button>Ajouter une vidéo et publier maintenant</button>']){
   await page.setContent('<main><h1>Créer un Reel</h1><input type="file" accept="video/*" hidden><input type="file" accept="video/mp4" hidden>'+controls+'</main>');
   data=await page.evaluate(dom,args);assert.equal(data.file_input_count,2);assert.equal(data.use_upload_chooser,false);assert.equal(data.files.length,2,'Missing, duplicate or disabled upload actions must preserve ambiguity');
  }
  await page.setContent('<main><h1>Créer un Reel</h1><div role="dialog"><h2>Créer une publication</h2><input type="file" accept="video/*"><input type="file" accept="video/*"><button>Importer une vidéo</button></div></main>');
  data=await page.evaluate(dom,args);assert.equal(data.reel_context,false);assert.equal(data.use_upload_chooser,false);assert.equal(data.files.length,0,'A regular post cannot select a Reel uploader');
  for(const labels of [
    {accessible:'Publish Reel',visible:'Publish'},
    {accessible:'Publish video to your Page',visible:'Publish'},
    {accessible:'Share',visible:'Share'},
    {accessible:'Share Reel',visible:'Share Reel'},
    {accessible:'Chia sẻ',visible:'Chia sẻ'}
  ]){
    await page.setContent('<main><h1>Create reel</h1><a href="/profile.php?id=555">Fixture Page</a><textarea aria-label="Caption">Fixture caption</textarea><button aria-label="'+labels.accessible+'">'+labels.visible+'</button></main>');
    data=await page.evaluate(dom,args);
    assert.equal(data.buttons.filter(item=>item.kind==='publish').length,1,'Known visible or accessible Publish/Share label works only in exact Reel context');
    assert.equal(data.buttons.find(item=>item.kind==='publish').label,labels.accessible,'Metadata retains actual accessible name');
  }
  await page.setContent('<main><h1>Create reel</h1><a href="/profile.php?id=555">Fixture Page</a><div role="progressbar">70%</div><textarea placeholder="Write a description">Wrong caption</textarea><button>Publish</button></main>');
  data=await page.evaluate(dom,args);
  assert.equal(data.processing,true);assert.equal(data.upload_ready,false);assert.equal(data.captions[0].matches,false);
  await page.setContent('<main><h1>Create reel</h1><a href="/profile.php?id=999">Fixture Page</a><a href="https://evil.test/Page"><img alt="">Fixture Page</a><textarea aria-label="Caption"></textarea><button>Publish</button></main>');
  data=await page.evaluate(dom,args);assert.equal(data.actor_matches,false,'Same-name conflicting/external profile links cannot verify the Page actor');
  await page.setContent('<main><h1>Create reel</h1><button aria-label="Fixture Page"><img alt=""></button><div contenteditable="true" role="textbox" aria-label="Caption">Fixture caption</div><button>Publish</button></main>');
  data=await page.evaluate(dom,args);assert.equal(data.actor_matches,true);assert.equal(data.captions[0].matches,true);
  const bareComposerAvatar='<button aria-label="Fixture Page"><img width="32" height="32" alt=""></button>';
  const composerActorRow=content=>'<div style="display:flex;align-items:center;gap:8px;width:320px;height:48px">'+content+'</div>';
  async function composerActorCase(name,identity,expected,extra=''){
   await page.setContent('<main><h1>Create reel</h1>'+identity+'<div contenteditable="true" role="textbox" aria-label="Caption">Fixture caption</div>'+extra+'<button>Publish</button></main>');
   const value=await page.evaluate(dom,args);
   assert.equal(value.reel_context,true,name+' keeps the Reel composer');
   assert.equal(value.actor_matches,expected,name);
   assert.equal(value.ui_diagnostics.actor_composer_conflicting_link_count>0,!expected,name+' conflict diagnostic');
  }
  await composerActorCase('Exact linked Page and its avatar retain actor proof',composerActorRow('<a href="/profile.php?id=555"><span role="button" aria-label="Fixture Page"><img width="32" height="32" alt=""></span>Fixture Page</a>'),true);
  await composerActorCase('Bare same-name avatar remains usable without contrary identity',composerActorRow(bareComposerAvatar),true);
  await composerActorCase('Foreign author name and URL beside same-name avatar block fallback',composerActorRow(bareComposerAvatar+'<a href="/profile.php?id=999">Other Page</a>'),false);
  await composerActorCase('Same-name author link with a foreign UID blocks fallback',bareComposerAvatar+'<a href="/profile.php?id=999">Fixture Page</a>',false);
  await composerActorCase('Same-name nested avatar cannot override its foreign anchor',composerActorRow('<a href="/profile.php?id=999"><span role="button" aria-label="Fixture Page"><img width="32" height="32" alt=""></span>Other Page</a>'),false);
  await composerActorCase('Caption mentions do not become author identities',composerActorRow(bareComposerAvatar),true,'<div contenteditable="true" role="textbox" aria-label="Tagged people"><a href="/profile.php?id=999">Fixture Page</a></div>');
  await composerActorCase('Unrelated body profile link is not an actor signal',composerActorRow(bareComposerAvatar),true,'<p>Tagged person: <a href="/profile.php?id=999">Other Page</a></p>');
  await composerActorCase('Same-name tagged body profile cannot become an actor contradiction',composerActorRow(bareComposerAvatar),true,'<p>Tagged person: <a href="/profile.php?id=999">Fixture Page</a></p>');
  await composerActorCase('Tagged body profile picture is not the composer actor',composerActorRow(bareComposerAvatar),true,'<p>Tagged person: <a href="/profile.php?id=999"><img width="32" height="32" alt="">Other Page</a></p>');
  await composerActorCase('Hidden contradictory header link is ignored',composerActorRow(bareComposerAvatar+'<a hidden href="/profile.php?id=999">Other Page</a>'),true);
  await composerActorCase('Navigation profile link cannot contradict composer identity',composerActorRow(bareComposerAvatar),true,'<nav><a href="/profile.php?id=999">Fixture Page</a></nav>');
  for(const mention of ['<a href="/profile.php?id=555">Fixture Page</a>',bareComposerAvatar]){
   await page.setContent('<main><h1>Create reel</h1>'+composerActorRow('<a href="/profile.php?id=999"><img width="32" height="32" alt="">Other Page</a>')+'<div contenteditable="true" role="textbox" aria-label="Caption">Fixture caption '+mention+'</div><button>Publish</button></main>');
   data=await page.evaluate(dom,args);
   assert.equal(data.actor_matches,false,'A target Page mention/avatar inside the caption cannot replace the actual foreign author');
  }
  await page.setContent('<main><h1>Create reel</h1><div role="dialog"><h2>Create post</h2><textarea aria-label="Caption"></textarea><button>Publish</button></div></main>');
  data=await page.evaluate(dom,args);
  // A regular post dialog over a Reel route must not be used as a Reel composer.
  assert.equal(data.published,false);assert.equal(data.reel_context,false);assert.equal(data.buttons.some(item=>item.kind==='publish'),false);
  await page.setContent('<main><h1>Create reel</h1><div role="dialog"><h2>Create post</h2><button>Share</button><button>Share Reel</button><button>Chia sẻ</button><a href="/reels/create/">Create Reel</a></div></main>');
  data=await page.evaluate(dom,args);assert.equal(data.reel_context,false);assert.equal(data.buttons.length,0,'Unknown blocking regular-post dialog cannot expose Share or background Create Reel');
  const finalFailures=[];
  const frenchFinal=({heading='Gérer la Page',url=target,name='Fixture Page',backdrop='aria-hidden="true" inert',sidebarStyle='',feed=false,farLink=false,regularPost=false}={})=>{
   const sidebar='<aside style="position:fixed;left:0;top:55px;width:280px;min-height:400px;'+sidebarStyle+'"><h2 style="margin:0 0 8px">'+heading+'</h2><a style="display:block;'+(farLink?'margin-top:300px;':'')+'" href="'+url+'">'+name+'</a><button>Publish</button><button>Create Reel</button></aside>';
   return '<div '+backdrop+'>'+(feed?'<article role="article">'+sidebar+'</article>':sidebar)+'</div><main><h1>Fixture Page</h1></main><div role="dialog" aria-modal="true" style="position:fixed;left:340px;top:45px;width:600px;min-height:400px;background:white"><h2>'+(regularPost?'Créer une publication':'Paramètres du reel')+'</h2><div contenteditable="true" role="textbox" aria-label="Décrivez votre reel...">Fixture caption</div><button id="fr-final-publish">Publier</button></div>';
  };
  async function finalCase(name,options,input=args,expected=true,current=target){
   try{
    await page.goto(current);await page.setContent(frenchFinal(options));const value=await page.evaluate(dom,input);
    assert.equal(value.page_context_actor_matches,expected,name+' sidebar actor proof');assert.equal(value.actor_matches,expected,name+' combined actor proof');
    if(!options.regularPost){
     assert.equal(value.reel_context,true,name+' exact French Reel settings context');
     assert.equal(value.captions.length,1);assert.equal(value.captions[0].matches,true);
     const publish=value.buttons.filter(item=>item.kind==='publish');assert.equal(publish.length,1);
     assert.equal(publish[0].label,'Publier');assert.equal(await page.locator('[data-cm-reel="'+publish[0].id+'"]').getAttribute('id'),'fr-final-publish');
     assert.equal(value.buttons.some(item=>['create','app_menu'].includes(item.kind)),false,'Only modal controls remain actionable');
    }else{assert.equal(value.reel_context,false);assert.equal(value.buttons.some(item=>item.kind==='publish'),false)}
   }catch(error){finalFailures.push({name,message:error.message})}
  }
  await finalCase('French final settings uses rendered aria-hidden inert Page sidebar',{});
  await page.goto(target);await page.setContent(frenchFinal().replace('<div contenteditable="true"',composerActorRow(bareComposerAvatar+'<a href="/profile.php?id=999">Other Page</a>')+'<div contenteditable="true"'));
  data=await page.evaluate(dom,args);
  assert.equal(data.page_context_actor_matches,true,'Exact target backdrop remains independently recognized');
  assert.equal(data.actor_matches,false,'Explicit conflicting composer author cannot be rescued by the target Page backdrop');
  assert.equal(data.ui_diagnostics.actor_composer_conflicting_link_count,1);
  await page.setContent(frenchFinal().replace('<div contenteditable="true"',composerActorRow('<a href="/profile.php?id=999"><img width="32" height="32" alt="">Other Page</a>')+'<div contenteditable="true"'));
  data=await page.evaluate(dom,args);
  assert.equal(data.page_context_actor_matches,true,'Target Page backdrop remains recognized when composer has only a foreign linked avatar');
  assert.equal(data.actor_matches,false,'Foreign linked author alone blocks stale target-backdrop actor proof');
  assert.equal(data.ui_diagnostics.actor_composer_conflicting_link_count,1);
  await finalCase('English Manage Page heading is equally bound',{heading:'Manage Page'});
  await finalCase('Canonical target URL tolerates tracking on unchanged target',{},args,true,target+'&ref=fixture');
  await finalCase('No verified actor latch cannot trust background Page',{}, {...args,pageActorVerified:false},false);
  await finalCase('Viewer Page without Manage Page cannot prove actor',{heading:'Photos'},args,false);
  await finalCase('Wrong sidebar Page URL',{url:'https://www.facebook.com/profile.php?id=999'},args,false);
  await finalCase('Wrong sidebar Page name',{name:'Other Fixture Page'},args,false);
  await finalCase('Wrong expected Page name',{}, {...args,pageName:'Other Fixture Page'},false);
  await finalCase('Changed current Page URL',{},args,false,'https://www.facebook.com/profile.php?id=999');
  for(const sidebarStyle of ['display:none;','visibility:hidden;','opacity:0;'])await finalCase('CSS hidden sidebar '+sidebarStyle,{sidebarStyle},args,false);
  await finalCase('HTML hidden background is not physically rendered',{backdrop:'hidden'},args,false);
  await finalCase('Manage Page in a feed article is not sidebar identity',{feed:true},args,false);
  await finalCase('Page link must be immediately below Manage Page',{farLink:true},args,false);
  await finalCase('Regular post modal cannot inherit Reel Page actor',{regularPost:true},args,false);
  const frenchNoLinkFinal=({manage='Gérer la Page',name='Fixture Page',heading='h1',place='main',backdrop='aria-hidden="true" inert',sidebarStyle='',mainStyle='',regularPost=false}={})=>{
   const title=heading==='aria1'?'<div role="heading" aria-level="1">'+name+'</div>':heading==='aria2'?'<div role="heading" aria-level="2">'+name+'</div>':heading==='h2'?'<h2>'+name+'</h2>':heading==='h3'?'<h3>'+name+'</h3>':'<h1>'+name+'</h1>';
   let mainTitle=place==='main'?title:place==='duplicate'?title+title:place==='feed'?'<article role="article">'+title+'</article>':place==='menu'?'<div role="menu">'+title+'</div>':place==='tooltip'?'<div role="tooltip">'+title+'</div>':'';
   return '<div '+backdrop+'><aside style="position:fixed;left:0;top:55px;width:280px;min-height:400px;'+sidebarStyle+'"><h2 style="margin:0">'+manage+'</h2><div>Identité de la Page</div><button>Tableau de bord professionnel</button></aside><main style="position:fixed;left:340px;top:55px;width:700px;min-height:400px;'+mainStyle+'">'+mainTitle+'</main></div><div role="dialog" aria-modal="true" style="position:fixed;left:340px;top:45px;width:600px;min-height:400px;background:white"><h2>'+(regularPost?'Créer une publication':'Paramètres du reel')+'</h2>'+(place==='composer'?title:'')+'<div contenteditable="true" role="textbox" aria-label="Décrivez votre reel...">Fixture caption</div><button id="fr-no-link-publish">Publier</button></div>';
  };
  async function noLinkCase(name,options={},input=args,expected=true,current=target){
   try{
    await page.goto(current);await page.setContent(frenchNoLinkFinal(options));const value=await page.evaluate(dom,input);
    assert.equal(await page.locator('aside a[href]').count(),0,'Fixture sidebar really has no Page href');
    assert.equal(value.page_context_actor_matches,expected,name+' linkless profile actor proof');assert.equal(value.actor_matches,expected,name+' combined actor proof');
    if(!options.regularPost){
     assert.equal(value.reel_context,true);assert.equal(value.captions.length,1);assert.equal(value.captions[0].matches,true);
     assert.equal(value.buttons.filter(item=>item.kind==='publish').length,1);
     assert.equal(value.buttons.filter(item=>['create','app_menu'].includes(item.kind)).length,0);
    }else{assert.equal(value.reel_context,false);assert.equal(value.buttons.filter(item=>item.kind==='publish').length,0)}
   }catch(error){finalFailures.push({name,message:error.message})}
  }
  await noLinkCase('Linkless French sidebar with exact profile h1 under inert aria-hidden backdrop');
  await noLinkCase('Linkless Manage Page with profile aria-level1 heading',{manage:'Manage Page',heading:'aria1'});
  await noLinkCase('Linkless profile requires verified actor latch',{}, {...args,pageActorVerified:false},false);
  await noLinkCase('Linkless profile requires unchanged exact current Page route',{},args,false,'https://www.facebook.com/profile.php?id=999');
  await noLinkCase('Linkless profile rejects different main heading name',{name:'Other Fixture Page'},args,false);
  await noLinkCase('Linkless profile rejects partial main heading name',{name:'Fixture Page · Publications'},args,false);
  await noLinkCase('Linkless profile requires separate Manage Page sidebar',{manage:'Photos'},args,false);
  for(const place of ['feed','menu','tooltip','composer'])await noLinkCase('Same-name heading only in '+place,{place},args,false);
  for(const heading of ['aria2','h2','h3'])await noLinkCase('Exact unique profile main heading can use '+heading,{heading});
  await noLinkCase('Duplicate same-name main headings are not unique',{heading:'aria2',place:'duplicate'},args,false);
  for(const sidebarStyle of ['display:none;','visibility:hidden;','opacity:0;'])await noLinkCase('CSS hidden linkless Manage sidebar '+sidebarStyle,{sidebarStyle},args,false);
  for(const mainStyle of ['display:none;','visibility:hidden;','opacity:0;'])await noLinkCase('CSS hidden linkless profile heading '+mainStyle,{mainStyle},args,false);
  await noLinkCase('Hidden linkless background cannot prove Page actor',{backdrop:'hidden'},args,false);
  await noLinkCase('Regular post cannot use linkless Page fallback',{regularPost:true},args,false);
  const frenchSidebarHeadingFinal=({manage='Gérer la Page',name='Fixture Page',heading='h2',place='sidebar',backdrop='aria-hidden="true" inert',sidebarStyle='',headingStyle='',gap=8,duplicate=false,hiddenMain=false,conflictingHref='',wrapperDepth=0,dashboardHref='',regularPost=false}={})=>{
   const wrap=html=>'<div>'.repeat(wrapperDepth)+html+'</div>'.repeat(wrapperDepth);
   const title=heading==='aria2'?'<div role="heading" aria-level="2" style="margin:0;margin-top:'+gap+'px;'+headingStyle+'">'+name+'</div>':heading==='text'?'<span style="display:block;margin-top:'+gap+'px;'+headingStyle+'">'+name+'</span>':'<h2 style="margin:0;margin-top:'+gap+'px;'+headingStyle+'">'+name+'</h2>';
   const sidebarTitle=place==='sidebar'?title+(duplicate?title:''):place==='menu'?'<div role="menu">'+title+'</div>':place==='feed'?'<article role="article">'+title+'</article>':place==='tooltip'?'<div role="tooltip">'+title+'</div>':'';
   const contradiction=conflictingHref?'<a href="'+conflictingHref+'" style="display:block">Fixture Page</a>':'';
   const dashboard=dashboardHref?'<a href="'+dashboardHref+'" style="display:block">Tableau de bord professionnel</a>':'';
   return '<div '+backdrop+'><aside style="position:fixed;left:0;top:55px;width:280px;min-height:400px;'+sidebarStyle+'">'+wrap('<h2 style="margin:0">'+manage+'</h2>')+wrap(sidebarTitle)+contradiction+dashboard+'<button>Tableau de bord professionnel</button></aside>'+(place==='right'?'<section style="position:fixed;left:800px;top:85px;width:300px">'+title+'</section>':'')+(hiddenMain?'<main style="display:none"><h1>Fixture Page</h1></main>':'')+'</div><div role="dialog" aria-modal="true" style="position:fixed;left:340px;top:45px;width:600px;min-height:400px;background:white"><h2>'+(regularPost?'Créer une publication':'Paramètres du reel')+'</h2>'+(place==='composer'?title:'')+'<div contenteditable="true" role="textbox" aria-label="Décrivez votre reel...">Fixture caption</div><button id="fr-sidebar-heading-publish">Publier</button></div>';
  };
  async function sidebarHeadingCase(name,options={},input=args,expected=true,current=target){
   try{
    await page.goto(current);await page.setContent(frenchSidebarHeadingFinal(options));const value=await page.evaluate(dom,input);
    assert.equal(await page.locator('main:visible h1').count(),0,'Fixture has no visible main profile header');
    assert.equal(await page.locator('[role="dialog"] a[href]').count(),0,'Final modal has no Page author link');
    assert.equal(await page.locator('aside a[href]').count(),Number(Boolean(options.conflictingHref))+Number(Boolean(options.dashboardHref)),'Sidebar heading fixtures do not borrow Page href proof');
    assert.equal(value.page_context_actor_matches,expected,name+' bounded sidebar heading proof');assert.equal(value.actor_matches,expected,name+' combined actor proof');
    if(!options.regularPost){
     assert.equal(value.reel_context,true);assert.equal(value.captions.length,1);assert.equal(value.captions[0].matches,true);
     const publish=value.buttons.filter(item=>item.kind==='publish');assert.equal(publish.length,1);assert.equal(publish[0].label,'Publier');
     assert.equal(await page.locator('[data-cm-reel="'+publish[0].id+'"]').getAttribute('id'),'fr-sidebar-heading-publish');
     assert.equal(value.buttons.some(item=>['create','app_menu'].includes(item.kind)),false);
    }else{assert.equal(value.reel_context,false);assert.equal(value.buttons.some(item=>item.kind==='publish'),false)}
   }catch(error){finalFailures.push({name,message:error.message})}
  }
  await sidebarHeadingCase('French final modal binds exact nearby sidebar h2 without main header');
  await sidebarHeadingCase('French final modal binds exact nearby sidebar role heading level2',{heading:'aria2'});
  await sidebarHeadingCase('Retained sidebar heading works when main profile header is CSS hidden',{hiddenMain:true});
  await sidebarHeadingCase('English Manage Page binds the same sidebar h2',{manage:'Manage Page'});
  await sidebarHeadingCase('Sidebar heading just within 200px remains bound',{gap:190});
  await sidebarHeadingCase('Twelve nested Facebook wrappers retain common bounded sidebar identity',{wrapperDepth:12});
  await sidebarHeadingCase('Nearby French professional dashboard link is navigation, not Page identity',{dashboardHref:'/professional_dashboard'});
  await sidebarHeadingCase('Deep sidebar wrappers and professional dashboard navigation retain heading proof',{wrapperDepth:12,dashboardHref:'/professional_dashboard/'});
  await sidebarHeadingCase('Deep role heading2 identity ignores professional dashboard navigation query',{wrapperDepth:12,heading:'aria2',dashboardHref:'/professional_dashboard?ref=fixture'});
  await sidebarHeadingCase('Sidebar heading requires verified actor latch',{}, {...args,pageActorVerified:false},false);
  await sidebarHeadingCase('Sidebar heading requires unchanged exact Page route',{},args,false,'https://www.facebook.com/profile.php?id=999');
  await sidebarHeadingCase('Sidebar heading rejects wrong Page name',{name:'Other Fixture Page'},args,false);
  await sidebarHeadingCase('Sidebar heading rejects wrong expected Page name',{}, {...args,pageName:'Other Fixture Page'},false);
  await sidebarHeadingCase('Sidebar heading rejects partial Page name',{name:'Fixture Page · Publications'},args,false);
  await sidebarHeadingCase('Sidebar heading requires Manage Page context',{manage:'Photos'},args,false);
  await sidebarHeadingCase('Distant sidebar Page heading over 200px is not identity',{gap:205},args,false);
  await sidebarHeadingCase('Right-hand unrelated Page heading cannot join left Manage Page',{place:'right'},args,false);
  await sidebarHeadingCase('Right-hand Manage Page panel is not left sidebar',{sidebarStyle:'left:800px;'},args,false);
  await sidebarHeadingCase('Duplicate same-name sidebar headings are ambiguous',{duplicate:true},args,false);
  for(const place of ['menu','feed','tooltip','composer'])await sidebarHeadingCase('Page heading only in '+place+' is not retained sidebar identity',{place},args,false);
  await sidebarHeadingCase('Plain sidebar text is not Page heading proof',{heading:'text'},args,false);
  for(const sidebarStyle of ['display:none;','visibility:hidden;','opacity:0;'])await sidebarHeadingCase('CSS hidden retained sidebar '+sidebarStyle,{sidebarStyle},args,false);
  for(const headingStyle of ['display:none;','visibility:hidden;','opacity:0;'])await sidebarHeadingCase('CSS hidden retained Page heading '+headingStyle,{headingStyle},args,false);
  await sidebarHeadingCase('HTML hidden retained sidebar background is not rendered',{backdrop:'hidden'},args,false);
  await sidebarHeadingCase('Contradictory linked sidebar identity blocks heading fallback',{conflictingHref:'https://www.facebook.com/profile.php?id=999'},args,false);
  await sidebarHeadingCase('Deep wrapper navigation cannot mask contradictory linked Page UID',{wrapperDepth:12,dashboardHref:'/professional_dashboard',conflictingHref:'https://www.facebook.com/profile.php?id=999'},args,false);
  await sidebarHeadingCase('Deep wrapper dashboard navigation cannot validate wrong heading name',{wrapperDepth:12,dashboardHref:'/professional_dashboard',name:'Other Fixture Page'},args,false);
  await sidebarHeadingCase('Deep wrapper dashboard navigation cannot bind distant Page heading',{wrapperDepth:12,dashboardHref:'/professional_dashboard',gap:205},args,false);
  await sidebarHeadingCase('Regular post cannot use retained sidebar heading proof',{regularPost:true},args,false);
  assert.deepEqual(finalFailures,[],'French final settings actor fixtures');
  await page.setContent('<main><div role="status"><h2>Your reel has been published</h2><a href="https://m.facebook.com/reel/123456?ref=tracking">View reel</a><a href="https://evil.test/reel/999">Fake destination</a></div></main>');
  data=await page.evaluate(dom,args);assert.equal(data.published,true);assert.deepEqual(data.published_urls,['https://www.facebook.com/reel/123456']);
  await page.goto('https://www.facebook.com/reel/123456');
  await page.setContent('<main><article role="article"><video width="300" height="400"></video><a href="/profile.php?id=555">Fixture Page</a><p>Fixture caption</p></article><aside><a href="/profile.php?id=999">Other Page</a></aside></main>');
  data=await page.evaluate(dom,args);assert.equal(data.current_reel_url,'https://www.facebook.com/reel/123456');assert.equal(data.actor_matches,true);assert.equal(data.published_caption_matches,true);
  await page.setContent('<main><article role="article"><video width="300" height="400"></video><a href="/profile.php?id=999">Other Page</a><p>Wrong caption</p></article><aside><a href="/profile.php?id=555">Fixture Page</a><p>Fixture caption</p></aside></main>');
  data=await page.evaluate(dom,args);assert.equal(data.actor_matches,false);assert.equal(data.published_caption_matches,false,'Recommended sidebar content cannot prove the current Reel');
  const permalinkFailures=[];
  const reelArticle=(style='',actor='555',text='Fixture caption')=>'<article role="article" style="'+style+'"><video width="300" height="400"></video><a href="/profile.php?id='+actor+'">'+(actor==='555'?'Fixture Page':'Other Page')+'</a><p>'+text+'</p></article>';
  async function permalinkCase(name,html,actorMatches,captionMatches,options={}){
   try{
    await page.setContent(options.bare?html:'<main>'+html+'</main>');const value=await page.evaluate(dom,options.input||args);
    assert.equal(value.current_reel_url,options.currentReelUrl??'https://www.facebook.com/reel/123456');
    assert.equal(value.actor_matches,actorMatches,name+' current Reel author proof');
    assert.equal(value.published_caption_matches,captionMatches,name+' current Reel caption proof');
    assert.equal(value.published,false,'Loaded Reel content alone is not a publication confirmation toast');
    if(options.check)options.check(value);
   }catch(error){permalinkFailures.push({name,message:error.message})}
  }
  for(const style of ['position:fixed;top:2000px;left:0;','position:fixed;top:-1000px;left:0;','position:fixed;top:0;left:2000px;','position:fixed;top:0;left:-1000px;'])await permalinkCase('Offscreen preloaded Reel '+style,reelArticle('', '555')+reelArticle(style,'999','Other caption'),true,true);
  await permalinkCase('Two onscreen Reel videos remain ambiguous',reelArticle('position:fixed;left:0;top:0;')+reelArticle('position:fixed;left:500px;top:0;','999','Other caption'),false,false);
  await permalinkCase('Offscreen-only target Reel cannot prove publication',reelArticle('position:fixed;top:2000px;left:0;'),false,false);
  await permalinkCase('Preloaded target identity cannot prove onscreen other author',reelArticle('','999','Wrong caption')+reelArticle('position:fixed;top:2000px;left:0;'),false,false);
  await permalinkCase('Offscreen duplicate cannot replace current wrong caption',reelArticle('','555','Wrong caption')+reelArticle('position:fixed;top:2000px;left:0;'),true,false);
  const reelVideo='<video width="300" height="400"></video>';
  const reelAuthor='<a href="/profile.php?id=555">Fixture Page</a>';
  const reelCaption='<p>Fixture caption</p>';
  const otherAuthor='<a href="/profile.php?id=999">Other Page</a>';
  const reelDialog=content=>'<div role="dialog" aria-modal="true">'+content+'</div>';
  await permalinkCase('Dialog-only Reel viewer binds exact Page href and caption',reelDialog(reelVideo+reelAuthor+reelCaption),true,true,{bare:true});
  await permalinkCase('Main inside dialog remains the nearest Reel boundary',reelDialog('<main>'+reelVideo+reelAuthor+reelCaption+'</main>'),true,true,{bare:true});
  await permalinkCase('Role main inside dialog remains the nearest Reel boundary',reelDialog('<div role="main">'+reelVideo+reelAuthor+reelCaption+'</div>'),true,true,{bare:true});
  await permalinkCase('Dialog outside background main is the current viewer','<main aria-hidden="true"><h1>Background Page</h1></main>'+reelDialog(reelVideo+reelAuthor+reelCaption),true,true,{bare:true});
  await permalinkCase('Dialog viewer stays narrower than surrounding main',otherAuthor+'<p>Wrong caption</p>'+reelDialog(reelVideo+reelAuthor+reelCaption),true,true);
  await permalinkCase('Native dialog Reel viewer is supported','<dialog open>'+reelVideo+reelAuthor+reelCaption+'</dialog>',true,true,{bare:true});
  await permalinkCase('Native article is a bounded Reel viewer','<article>'+reelVideo+reelAuthor+reelCaption+'</article>'+otherAuthor+'<p>Wrong caption</p>',true,true);
  await permalinkCase('Dialog wrong Page cannot verify target',reelDialog(reelVideo+otherAuthor+'<p>Wrong caption</p>'),false,false,{bare:true});
  await permalinkCase('Dialog exact Page with wrong caption stays unconfirmed',reelDialog(reelVideo+reelAuthor+'<p>Wrong caption</p>'),true,false,{bare:true});
  await permalinkCase('Permalink needs exact target Page href, not same-name avatar button',reelDialog(reelVideo+'<button aria-label="Fixture Page"><img alt=""></button>'+reelCaption),false,false,{bare:true});
  await permalinkCase('Permalink needs exact Page name on target href',reelDialog(reelVideo+'<a href="/profile.php?id=555">Other Page</a>'+reelCaption),false,true,{bare:true});
  await permalinkCase('Verbose aria label does not override exact visible Page author',reelDialog(reelVideo+'<a href="/profile.php?id=555" aria-label="Open the profile of Fixture Page">Fixture Page</a>'+reelCaption),true,true,{bare:true});
  await permalinkCase('Conflicting visible author cannot borrow an exact aria name',reelDialog(reelVideo+'<a href="/profile.php?id=555" aria-label="Fixture Page">Other Page</a>'+reelCaption),false,true,{bare:true});
  await permalinkCase('Image-only author href may use its exact accessible name',reelDialog(reelVideo+'<a href="/profile.php?id=555" aria-label="Fixture Page"><img width="32" height="32" alt=""></a>'+reelCaption),true,true,{bare:true});
  await permalinkCase('Empty text-only author href cannot fabricate name through aria',reelDialog(reelVideo+'<a href="/profile.php?id=555" aria-label="Fixture Page" style="display:inline-block;width:32px;height:32px"></a>'+reelCaption),false,true,{bare:true});
  await permalinkCase('Permalink requires a nonempty expected Page name',reelDialog(reelVideo+reelAuthor+reelCaption),false,true,{bare:true,input:{...args,pageName:''}});
  for(const region of ['aside','nav','div role="complementary"','div role="navigation"','div role="menu"','div role="tooltip"']){
   const tag=region.split(' ')[0],recommendation='<'+region+'>'+reelAuthor+reelCaption+'</'+tag+'>';
   await permalinkCase('Main cannot borrow exact Page and caption from '+region,'<section>'+reelVideo+otherAuthor+'<p>Wrong caption</p></section>'+recommendation,false,false);
   await permalinkCase('Caption aggregate cannot re-import '+region,'<article role="article">'+reelVideo+reelAuthor+'<div><'+region+'>'+reelCaption+'</'+tag+'></div></article>',true,false);
  }
  await permalinkCase('Main cannot borrow proof from nested comment article','<section>'+reelVideo+otherAuthor+'<p>Wrong caption</p></section><article role="article" aria-label="Comment">'+reelAuthor+reelCaption+'</article>',false,false);
  await permalinkCase('Reel article cannot borrow nested comment author or caption','<article role="article">'+reelVideo+otherAuthor+'<p>Wrong caption</p><article role="article" aria-label="Comment">'+reelAuthor+reelCaption+'</article></article>',false,false);
  await permalinkCase('Caption aggregate cannot re-import nested comment article','<article role="article">'+reelVideo+reelAuthor+'<div><article role="article" aria-label="Comment">'+reelCaption+'</article></div></article>',true,false);
  await permalinkCase('Caption aggregate cannot re-import a separate main boundary','<article role="article">'+reelVideo+reelAuthor+'<div><main>'+reelCaption+'</main></div></article>',true,false);
  await permalinkCase('Caption aggregate cannot re-import an editable caption','<article role="article">'+reelVideo+reelAuthor+'<div><div contenteditable="true">Fixture caption</div></div></article>',true,false);
  await permalinkCase('Conflicting plain-div comment Page cannot verify wrong Reel author',reelDialog(reelVideo+otherAuthor+'<p>Wrong caption</p><div>'+reelAuthor+reelCaption+'</div>'),false,false,{bare:true});
  await permalinkCase('Conflicting plain-div comment caption leaves target Reel unconfirmed',reelDialog(reelVideo+reelAuthor+'<p>Wrong caption</p><div>'+otherAuthor+reelCaption+'</div>'),false,false,{bare:true});
  await permalinkCase('Plain-div conflicting identity remains fail-closed even with matching Reel caption',reelDialog(reelVideo+reelAuthor+reelCaption+'<div>'+otherAuthor+'<p>Other comment</p></div>'),false,false,{bare:true});
  await permalinkCase('Duplicate links to the same canonical Page are not distinct identities',reelDialog(reelVideo+reelAuthor+reelCaption+'<a href="https://m.facebook.com/profile.php?id=555&ref=fixture">Fixture Page</a>'),true,true,{bare:true});
  await permalinkCase('Excluded navigation identities do not conflict with current Reel',reelDialog(reelVideo+reelAuthor+reelCaption+'<nav>'+otherAuthor+'</nav>'),true,true,{bare:true});
  const avatarButton=name=>'<button aria-label="'+name+'"><img width="32" height="32" alt=""></button>';
  await permalinkCase('Unlinked wrong author avatar cannot borrow target commenter proof',reelDialog(reelVideo+avatarButton('Other Page')+'<p>Wrong caption</p><div>'+reelAuthor+reelCaption+'</div>'),false,false,{bare:true,check:value=>{
   assert.equal(value.ui_diagnostics.scope_reel_unlinked_avatar_count,1);assert.equal(value.ui_diagnostics.scope_reel_conflicting_avatar_count,1);
  }});
  await permalinkCase('Same-name unlinked avatar leaves linked comment ownership ambiguous',reelDialog(reelVideo+avatarButton('Fixture Page')+'<p>Wrong caption</p><div>'+reelAuthor+reelCaption+'</div>'),false,false,{bare:true,check:value=>{
   assert.equal(value.ui_diagnostics.scope_reel_unlinked_avatar_count,1);assert.equal(value.ui_diagnostics.scope_reel_conflicting_avatar_count,0);
  }});
  const linkedAvatar=(href,name,alt='')=>'<a href="'+href+'"><span role="button" aria-label="Profile picture"><img width="32" height="32" alt="'+alt+'"></span><span>'+name+'</span></a>';
  await permalinkCase('Avatar button inside exact target author link is one linked identity',reelDialog(reelVideo+linkedAvatar('/profile.php?id=555','Fixture Page','Fixture Page')+reelCaption),true,true,{bare:true,check:value=>{
   assert.equal(value.ui_diagnostics.scope_reel_nested_profile_link_count,1);assert.equal(value.ui_diagnostics.scope_reel_nested_target_link_count,1);
   assert.equal(value.ui_diagnostics.scope_reel_unlinked_avatar_count,0);assert.equal(value.ui_diagnostics.scope_reel_conflicting_avatar_count,0);
   assert.equal(value.ui_diagnostics.scope_reel_image_alt_target_count,1);assert.equal(value.ui_diagnostics.scope_reel_wrapper_exact_target_name_count,1);
  }});
  await permalinkCase('Empty-alt nested target avatar retains exact parent author proof',reelDialog(reelVideo+linkedAvatar('/profile.php?id=555','Fixture Page')+reelCaption),true,true,{bare:true,check:value=>{
   assert.equal(value.ui_diagnostics.scope_reel_image_empty_alt_count,1);assert.equal(value.ui_diagnostics.scope_reel_image_alt_other_count,0);
  }});
  await permalinkCase('Wrong Page linked avatar cannot borrow target commenter proof',reelDialog(reelVideo+linkedAvatar('/profile.php?id=999','Other Page','Other Page')+'<p>Wrong caption</p><div>'+reelAuthor+reelCaption+'</div>'),false,false,{bare:true,check:value=>{
   assert.equal(value.ui_diagnostics.scope_reel_nested_profile_link_count,1);assert.equal(value.ui_diagnostics.scope_reel_nested_target_link_count,0);
   assert.equal(value.ui_diagnostics.scope_reel_profile_identity_count,2);assert.equal(value.ui_diagnostics.scope_reel_image_alt_other_count,1);
  }});
  for(const href of ['https://evil.test/profile.php?id=555','https://www.facebook.com:444/profile.php?id=555','javascript:void(0)'])await permalinkCase('Invalid avatar link ancestor remains ambiguous '+href,reelDialog(reelVideo+linkedAvatar(href,'Fixture Page')+'<p>Wrong caption</p><div>'+reelAuthor+reelCaption+'</div>'),false,false,{bare:true,check:value=>{
   assert.equal(value.ui_diagnostics.scope_reel_nested_profile_link_count,0);assert.equal(value.ui_diagnostics.scope_reel_unlinked_avatar_count,1);
  }});
  await permalinkCase('Nested avatar does not override conflicting visible parent author name',reelDialog(reelVideo+'<a href="/profile.php?id=555" aria-label="Fixture Page"><span role="button" aria-label="Profile picture"><img width="32" height="32" alt=""></span>Other Page</a>'+reelCaption),false,true,{bare:true});
  await permalinkCase('Anonymous unlinked avatar cannot make a commenter the author',reelDialog(reelVideo+avatarButton('')+'<p>Wrong caption</p><div>'+reelAuthor+reelCaption+'</div>'),false,false,{bare:true});
  await permalinkCase('Conflicting visible SVG avatar identity is negative evidence',reelDialog(reelVideo+'<button aria-label="Other Page"><svg role="img" width="32" height="32"></svg></button><p>Wrong caption</p><div>'+reelAuthor+reelCaption+'</div>'),false,false,{bare:true});
  await permalinkCase('Known action SVG icon is not an author avatar',reelDialog(reelVideo+reelAuthor+reelCaption+'<button aria-label="Comment"><svg role="img" width="32" height="32"><path d="M0 0h24v24H0z"></path></svg></button>'),true,true,{bare:true,check:value=>{
   assert.equal(value.ui_diagnostics.scope_reel_svg_button_count,1);assert.equal(value.ui_diagnostics.scope_reel_svg_icon_button_count,1);
   assert.equal(value.ui_diagnostics.scope_reel_svg_image_button_count,0);assert.equal(value.ui_diagnostics.scope_reel_known_action_button_count,1);
   assert.equal(value.ui_diagnostics.scope_reel_excluded_action_button_count,1);assert.equal(value.ui_diagnostics.scope_reel_unlinked_avatar_count,0);
  }});
  await permalinkCase('SVG embedded-image avatar remains ambiguous despite UI action label',reelDialog(reelVideo+reelAuthor+reelCaption+'<button aria-label="Comment"><svg role="img" width="32" height="32"><image width="32" height="32" href="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\'/%3E"></image></svg></button>'),false,false,{bare:true,check:value=>{
   assert.equal(value.ui_diagnostics.scope_reel_svg_image_button_count,1);assert.equal(value.ui_diagnostics.scope_reel_excluded_action_button_count,0);
  }});
  await permalinkCase('Raster image button remains ambiguous despite known action label',reelDialog(reelVideo+reelAuthor+reelCaption+avatarButton('Comment')),false,false,{bare:true,check:value=>{
   assert.equal(value.ui_diagnostics.scope_reel_image_button_count,1);assert.equal(value.ui_diagnostics.scope_reel_known_action_button_count,1);
   assert.equal(value.ui_diagnostics.scope_reel_excluded_action_button_count,0);
  }});
  await permalinkCase('Form and adjacent textbox classify unknown image without inferring owner',reelDialog(reelVideo+reelAuthor+reelCaption+'<form><textarea aria-label="Comment"></textarea>'+avatarButton('Other Page')+'</form>'),false,false,{bare:true,check:value=>{
   assert.equal(value.ui_diagnostics.scope_reel_avatar_in_form_count,1);assert.equal(value.ui_diagnostics.scope_reel_avatar_near_textbox_count,1);
   assert.equal(value.ui_diagnostics.scope_reel_unlinked_avatar_count,1);assert.equal(value.ui_diagnostics.scope_reel_conflicting_avatar_count,1);
  }});
  await permalinkCase('Hidden avatar identity does not conflict with linked current author',reelDialog(reelVideo+reelAuthor+reelCaption+'<div hidden>'+avatarButton('Other Page')+'</div>'),true,true,{bare:true});
  await permalinkCase('Excluded navigation avatar does not conflict with linked current author',reelDialog(reelVideo+reelAuthor+reelCaption+'<nav>'+avatarButton('Other Page')+'</nav>'),true,true,{bare:true});
  await permalinkCase('Decorative hidden SVG is not an avatar identity',reelDialog(reelVideo+reelAuthor+reelCaption+'<button aria-label="Like"><svg role="img" aria-hidden="true" width="32" height="32"></svg></button>'),true,true,{bare:true});
  await permalinkCase('Profile link diagnostics distinguish rendered name from aria label',reelDialog(reelVideo+'<a href="/profile.php?id=555" aria-label="Other Page">Fixture Page</a>'+reelCaption),true,true,{bare:true,check:value=>{
   assert.equal(value.ui_diagnostics.scope_reel_eligible_profile_link_count,1);assert.equal(value.ui_diagnostics.scope_reel_target_href_count,1);
   assert.equal(value.ui_diagnostics.scope_reel_target_name_count,1);assert.equal(value.ui_diagnostics.scope_reel_target_href_in_viewer_count,1);
   assert.equal(value.ui_diagnostics.scope_reel_target_text_name_count,1);assert.equal(value.ui_diagnostics.scope_reel_target_labelled_count,1);
   assert.equal(value.ui_diagnostics.scope_reel_link_label_mismatch_count,1);assert.equal(value.ui_diagnostics.scope_reel_profile_identity_count,1);
  }});
  await permalinkCase('Excluded target href remains visible in bounded diagnostic counts',reelVideo+otherAuthor+'<p>Wrong caption</p><aside>'+reelAuthor+reelCaption+'</aside>',false,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_reel_eligible_profile_link_count,1);assert.equal(value.ui_diagnostics.scope_reel_target_href_count,0);
   assert.equal(value.ui_diagnostics.scope_reel_target_name_count,0);assert.equal(value.ui_diagnostics.scope_reel_target_href_in_viewer_count,1);
  }});
  await permalinkCase('Nondefault author profile port cannot verify target Page',reelDialog(reelVideo+'<a href="https://www.facebook.com:444/profile.php?id=555">Fixture Page</a>'+reelCaption),false,true,{bare:true});
  await permalinkCase('Credential-bearing author href cannot verify target Page',reelDialog(reelVideo+'<a href="https://fixture:fixture@www.facebook.com/profile.php?id=555">Fixture Page</a>'+reelCaption),false,true,{bare:true});
  await permalinkCase('Explicit standard HTTPS author port remains canonical',reelDialog(reelVideo+'<a href="https://www.facebook.com:443/profile.php?id=555">Fixture Page</a>'+reelCaption),true,true,{bare:true});
  const wrapComment=(html,depth)=>'<div>'.repeat(depth)+html+'</div>'.repeat(depth);
  const commentRow=({prompt='Commenter en tant que Fixture Page',attribute='aria-label',avatarName='Profile picture',extra='',width=600,height=120,visiblePrompt=false}={})=>{
   const field='<div role="textbox" contenteditable="true" '+attribute+'="'+prompt+'" style="width:290px;height:76px;background:#eee"></div>';
   return '<div style="display:flex;align-items:center;gap:8px;width:'+width+'px;height:'+height+'px">'+wrapComment(avatarButton(avatarName),6)+wrapComment(field,8)+(visiblePrompt?'<span>'+prompt+'</span>':'')+extra+'</div>';
  };
  for(const attribute of ['aria-label','data-placeholder'])await permalinkCase('Deep exact comment-as '+attribute+' row is excluded from avatar ambiguity',reelVideo+reelAuthor+reelCaption+commentRow({attribute}),true,true,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_exact_prompt_count,1);assert.equal(value.ui_diagnostics.scope_comment_composer_count,1);
   assert.equal(value.ui_diagnostics.scope_comment_excluded_avatar_count,1);assert.equal(value.ui_diagnostics.scope_reel_unlinked_avatar_count,0);
  }});
  await permalinkCase('Exact visible comment-as prompt identifies deep entry row',reelVideo+reelAuthor+reelCaption+commentRow({attribute:'data-unrelated',visiblePrompt:true}),true,true,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_composer_count,1);
  }});
  await permalinkCase('Textarea placeholder supplies exact rendered comment-as prompt',reelVideo+reelAuthor+reelCaption+'<div style="display:flex;align-items:center;gap:8px;width:600px;height:120px">'+wrapComment(avatarButton('Profile picture'),6)+wrapComment('<textarea placeholder="Comment as Fixture Page" style="width:290px;height:76px"></textarea>',8)+'</div>',true,true);
  for(const prompt of ['Comment as Fixture Page','Bình luận dưới tên Fixture Page','Bình luận với tư cách Fixture Page'])await permalinkCase('Exact localized comment-as prompt '+prompt,reelVideo+reelAuthor+reelCaption+commentRow({prompt}),true,true);
  await permalinkCase('Wrong author cannot borrow target comment composer Page or caption',reelVideo+otherAuthor+'<p>Wrong caption</p>'+commentRow({extra:'<div>'+reelAuthor+reelCaption+'</div>'}),false,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_composer_count,1);assert.equal(value.ui_diagnostics.scope_reel_target_href_count,0);
  }});
  await permalinkCase('Exact commenter caption cannot replace wrong published caption',reelVideo+reelAuthor+'<p>Wrong caption</p>'+commentRow({extra:'<div>'+reelCaption+'</div>'}),true,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_composer_count,1);
  }});
  await permalinkCase('Comment composer aggregate cannot re-import excluded caption',reelVideo+reelAuthor+'<div>'+commentRow({extra:reelCaption})+'</div>',true,false);
  await permalinkCase('Unbounded comment row does not excuse unknown avatar',reelVideo+reelAuthor+reelCaption+commentRow({width:900,height:240}),false,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_composer_count,0);assert.ok(value.ui_diagnostics.scope_comment_rejected_geometry_count>0);
   assert.equal(value.ui_diagnostics.scope_reel_unlinked_avatar_count,1);
  }});
  await permalinkCase('Separate sibling comment prompt cannot excuse another avatar row',reelVideo+reelAuthor+reelCaption+commentRow({attribute:'data-unrelated'})+'<p>Commenter en tant que Fixture Page</p>',false,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_composer_count,0);assert.equal(value.ui_diagnostics.scope_reel_unlinked_avatar_count,1);
  }});
  await permalinkCase('Comment-as other Page cannot excuse unknown avatar',reelVideo+reelAuthor+reelCaption+commentRow({prompt:'Commenter en tant que Other Page'}),false,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_exact_prompt_count,0);assert.equal(value.ui_diagnostics.scope_comment_composer_count,0);
  }});
  await permalinkCase('Partial comment-as Page name cannot bind entry region',reelVideo+reelAuthor+reelCaption+commentRow({prompt:'Commenter en tant que Fixture Page extra'}),false,false);
  await permalinkCase('Compact unknown author cannot be hidden by expected commenter prompt',reelVideo+'<p>Wrong caption</p>'+commentRow({avatarName:'Other Page'})+'<div>'+reelAuthor+reelCaption+'</div>',false,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_composer_count,0);assert.equal(value.ui_diagnostics.scope_comment_rejected_label_count,1);
  }});
  await permalinkCase('Compact shared wrapper cannot discard a second standalone avatar',reelVideo+'<p>Wrong caption</p>'+commentRow({extra:avatarButton('Profile picture')})+'<div>'+reelAuthor+reelCaption+'</div>',false,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_composer_count,0);assert.equal(value.ui_diagnostics.scope_reel_unlinked_avatar_count,2);
   assert.ok(value.ui_diagnostics.scope_comment_rejected_geometry_count>0);
  }});
  await permalinkCase('Conflicting Page link disqualifies bounded commenter region',reelVideo+reelAuthor+reelCaption+commentRow({extra:otherAuthor}),false,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_composer_count,0);
  }});
  await permalinkCase('Heading disqualifies broad shared commenter wrapper',reelVideo+reelAuthor+reelCaption+commentRow({extra:'<h3>Author section</h3>'}),false,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_composer_count,0);
  }});
  const lexicalCommentRow=({fieldAttribute='data-unrelated="true"',typed='',placeholder=true,placeholderFlags='aria-hidden="true"',placeholderStyle='',placeholderBoundary=false,avatarName='Profile picture'}={})=>{
   const field='<div role="textbox" contenteditable="true" '+fieldAttribute+' style="width:290px;height:76px;background:#eee">'+typed+'</div>';
   let hint=placeholder?'<div '+placeholderFlags+' style="position:absolute;left:0;top:0;'+placeholderStyle+'">'+wrapComment('<span>Commenter en tant que Fixture Page</span>',8)+'</div>':'';
   if(placeholderBoundary)hint='<article role="article">'+hint+'</article>';
   return '<div style="display:flex;align-items:center;gap:8px;width:600px;height:120px">'+wrapComment(avatarButton(avatarName),6)+wrapComment('<div style="position:relative;width:290px;height:76px">'+field+hint+'</div>',8)+'</div>';
  };
  await permalinkCase('Deep CSS-rendered aria-hidden Lexical placeholder identifies comment row',reelVideo+reelAuthor+reelCaption+lexicalCommentRow(),true,true,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_rendered_field_count,1);assert.equal(value.ui_diagnostics.scope_comment_attribute_prompt_count,0);
   assert.equal(value.ui_diagnostics.scope_comment_rendered_text_prompt_count,1);assert.equal(value.ui_diagnostics.scope_comment_a11y_hidden_prompt_count,1);
   assert.equal(value.ui_diagnostics.scope_comment_composer_count,1);assert.equal(value.ui_diagnostics.scope_comment_excluded_avatar_count,1);
  }});
  for(const attribute of ['aria-label','data-placeholder','aria-placeholder'])await permalinkCase('Populated editor purpose attribute '+attribute+' prevents typed caption borrowing',reelVideo+reelAuthor+'<p>Wrong caption</p>'+lexicalCommentRow({fieldAttribute:attribute+'="Commenter en tant que Fixture Page"',typed:reelCaption,placeholder:false}),true,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_attribute_prompt_count,1);assert.equal(value.ui_diagnostics.scope_comment_composer_count,1);
  }});
  await permalinkCase('Populated textarea placeholder still identifies comment editor',reelVideo+reelAuthor+'<p>Wrong caption</p><div style="display:flex;align-items:center;gap:8px;width:600px;height:120px">'+wrapComment(avatarButton('Profile picture'),6)+wrapComment('<textarea placeholder="Comment as Fixture Page" style="width:290px;height:76px">Fixture caption</textarea>',8)+'</div>',true,false);
  await permalinkCase('Ordinary typed comment-as text is not a placeholder classifier',reelVideo+reelAuthor+reelCaption+lexicalCommentRow({typed:'Commenter en tant que Fixture Page',placeholder:false}),false,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_rendered_field_count,1);assert.equal(value.ui_diagnostics.scope_comment_exact_prompt_count,0);
   assert.equal(value.ui_diagnostics.scope_comment_composer_count,0);
  }});
  for(const options of [{placeholderFlags:'hidden'},{placeholderFlags:'inert'},{placeholderStyle:'display:none;'},{placeholderStyle:'visibility:hidden;'},{placeholderStyle:'opacity:0;'},{placeholderStyle:'left:2000px;'},{placeholderBoundary:true}])await permalinkCase('Nonrendered or other-boundary hint cannot classify comment row '+JSON.stringify(options),reelVideo+reelAuthor+reelCaption+lexicalCommentRow(options),false,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_exact_prompt_count,0);assert.equal(value.ui_diagnostics.scope_comment_composer_count,0);
  }});
  await permalinkCase('Aria-hidden author and caption remain ineligible despite placeholder exception',reelVideo+'<a href="/profile.php?id=555" aria-hidden="true">Fixture Page</a><p aria-hidden="true">Fixture caption</p>'+lexicalCommentRow(),false,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_composer_count,1);
  }});
  await permalinkCase('Rejected avatar label classifications remain numeric and negative',reelVideo+reelAuthor+reelCaption+lexicalCommentRow({avatarName:'Choose comment profile'}),false,false,{check:value=>{
   assert.equal(value.ui_diagnostics.scope_comment_rejected_label_count,1);assert.equal(value.ui_diagnostics.scope_comment_rejected_profile_word_count,1);
   assert.equal(value.ui_diagnostics.scope_comment_rejected_switch_word_count,1);assert.equal(value.ui_diagnostics.scope_comment_rejected_comment_word_count,1);
  }});
  for(const port of ['444','8443']){
   await page.goto('https://www.facebook.com:'+port+'/reel/123456');
   await permalinkCase('Nondefault current Reel port '+port+' has no permalink proof',reelVideo+reelAuthor+reelCaption,false,false,{currentReelUrl:''});
  }
  await page.goto('https://www.facebook.com:443/reel/123456');
  await permalinkCase('Explicit standard HTTPS Reel port remains canonical',reelVideo+reelAuthor+reelCaption,true,true);
  await page.setContent('<main><div role="status"><h2>Your reel has been published</h2><a href="https://www.facebook.com:444/reel/123456">Nondefault origin</a><a href="https://fixture:fixture@www.facebook.com/reel/123456">Credential origin</a></div></main>');
  data=await page.evaluate(dom,args);assert.equal(data.published,true);assert.deepEqual(data.published_urls,[],'Unsupported origin links cannot supply publication URLs');
  assert.deepEqual(permalinkFailures,[],'Current Reel viewport publication proof fixtures');
  const lightArgs={...args,verificationOnly:true},lightUrl='https://www.facebook.com/reel/123456';
  async function lightCase(name,html,count,url=lightUrl,expectedUrl=lightUrl){
   await page.goto(url);await page.setContent(html);
   const before=await page.content(),value=await page.evaluate(dom,lightArgs);
   assert.deepEqual(value,{blocked:false,current_reel_url:expectedUrl,reel_video_visible:count===1,reel_video_count:count},name);
   assert.equal(await page.content(),before,name+' never mutates the rendered DOM');
  }
  await lightCase('Light reconciliation accepts one visible video without author or caption','<main><video width="300" height="400"></video><p>Unrelated Page and text</p></main>',1);
  await lightCase('Light reconciliation needs a rendered video','<main><p>No video</p></main>',0);
  await lightCase('Light reconciliation excludes offscreen video','<main><video style="position:fixed;left:0;top:2000px;width:300px;height:400px"></video></main>',0);
  await lightCase('Light reconciliation excludes zero-sized video','<main><video style="width:0;height:0"></video></main>',0);
  await lightCase('Light reconciliation rejects two onscreen videos','<video style="position:fixed;left:0;top:0;width:300px;height:400px"></video><video style="position:fixed;left:500px;top:0;width:300px;height:400px"></video>',2);
  await lightCase('Preloaded offscreen video does not create ambiguity','<video width="300" height="400"></video><video style="position:fixed;left:0;top:2000px;width:300px;height:400px"></video>',1);
  await lightCase('Partially onscreen video remains rendered','<video style="position:fixed;left:-100px;top:0;width:300px;height:400px"></video>',1);
  for(const attribute of ['hidden','inert','aria-hidden="true"','style="display:none"','style="visibility:hidden"','style="opacity:0"'])await lightCase('Hidden video is absent from light proof '+attribute,'<div '+attribute+'><video width="300" height="400"></video></div>',0);
  for(const url of ['https://www.facebook.com:444/reel/123456','https://evil.test/reel/123456','http://www.facebook.com/reel/123456','https://www.facebook.com/reel/not-numeric'])await lightCase('Unsupported current origin or path cannot supply light proof '+url,'<video width="300" height="400"></video>',0,url,'');
  await lightCase('Standard HTTPS and mobile Reel URLs remain canonical','<video width="300" height="400"></video>',1,'https://m.facebook.com:443/reels/123456/?ref=fixture');
  await page.goto(lightUrl);await page.setContent('<main><video width="300" height="400"></video><a href="/profile.php?id=999" data-cm-reel="keep">Other Page</a><textarea aria-label="Caption">Unrelated caption</textarea><aside>Menu</aside></main>');
  const readOnlyBefore=await page.content();
  await page.evaluate(()=>{
   window.fixtureOriginalQuery=Document.prototype.querySelectorAll;window.fixtureOriginalWalker=document.createTreeWalker;
   Document.prototype.querySelectorAll=function(selector){
    if(!['input[name="email"],input[name="pass"]','h1,h2,h3,[role="heading"]','video'].includes(selector))throw new Error('Unexpected full DOM scan: '+selector);
    return window.fixtureOriginalQuery.call(this,selector);
   };
   document.createTreeWalker=()=>{throw new Error('Light reconciliation cannot scan page text')};
  });
  try{assert.deepEqual(await page.evaluate(dom,lightArgs),{blocked:false,current_reel_url:lightUrl,reel_video_visible:true,reel_video_count:1})}
  finally{await page.evaluate(()=>{Document.prototype.querySelectorAll=window.fixtureOriginalQuery;document.createTreeWalker=window.fixtureOriginalWalker;delete window.fixtureOriginalQuery;delete window.fixtureOriginalWalker})}
  assert.equal(await page.content(),readOnlyBefore,'Light reconciliation preserves fields, text and existing composer markers');
  const notice=(links='<a href="/reel/123456">View reel</a>',attributes='role="status" style="width:420px"',title='Your reel has been published')=>'<div '+attributes+'><h2>'+title+'</h2>'+links+'</div>';
  async function discoveryCase(name,html,count,url=count===1?lightUrl:''){
   await page.goto(target);await page.setContent(html);const before=await page.content();
   assert.deepEqual(await page.evaluate(dom,{...lightArgs,discoverPublishedReel:true}),{blocked:false,current_reel_url:'',reel_video_visible:false,reel_video_count:0,published_reel_candidate_url:url,published_reel_candidate_count:count},name);
   assert.equal(await page.content(),before,name+' observes links without clicking or mutating');
  }
  await discoveryCase('Fresh published status provides one official View Reel candidate',notice(),1);
  await discoveryCase('French bounded published dialog provides canonical mobile candidate',notice('<a href="https://m.facebook.com/reels/123456/?ref=fixture">Voir le reel</a>','role="dialog" style="width:420px"','Votre reel a été publié'),1);
  await discoveryCase('Duplicate aliases for the same Reel stay unambiguous',notice('<a href="/reel/123456">View reel</a><a href="https://m.facebook.com/reels/123456/?ref=fixture">See reel</a>'),1);
  await discoveryCase('Two different published Reel URLs stay ambiguous',notice('<a href="/reel/123456">View reel</a><a href="/reel/999">View reel</a>'),2);
  await discoveryCase('Generic feed Reel links are never discovered','<main><article role="article"><video width="300" height="400"></video><a href="/reel/123456">View reel</a></article></main>',0);
  await discoveryCase('Success words in a feed post cannot fabricate a notice','<article role="article">'+notice()+'</article>',0);
  await discoveryCase('Unscoped success heading and View Reel link are not a UI notice','<section><h2>Your reel has been published</h2><a href="/reel/123456">View reel</a></section>',0);
  await discoveryCase('Known notice cannot follow an unrelated action label',notice('<a href="/reel/123456">Open post</a>'),0);
  await discoveryCase('Notice containing feed content is too broad',notice('<article role="article"><a href="/reel/123456">View reel</a></article>'),0);
  await discoveryCase('Notice containing a video is not a bounded confirmation',notice('<video width="300" height="100"></video><a href="/reel/123456">View reel</a>'),0);
  await discoveryCase('Composer dialog cannot become a published-link region',notice('<textarea aria-label="Caption"></textarea><a href="/reel/123456">View reel</a>','role="dialog" style="width:420px"'),0);
  await discoveryCase('Unbounded notice panel is not followed',notice(undefined,'role="status" style="width:1000px;height:600px"'),0);
  await discoveryCase('Hidden publication status is not followed',notice(undefined,'role="status" hidden style="width:420px"'),0);
  await discoveryCase('Offscreen View Reel anchor is not an observed candidate',notice('<a href="/reel/123456" style="position:fixed;left:2000px;top:0">View reel</a>'),0);
  await discoveryCase('Invalid proof origins cannot be candidate links',notice('<a href="https://www.facebook.com:444/reel/123456">View reel</a><a href="https://fixture:fixture@www.facebook.com/reel/123456">View reel</a><a href="https://evil.test/reel/123456">View reel</a>'),0);
  await lightCase('Preexisting notice is not scanned without discovery opt-in',notice(),0,target,'');
  await page.goto(lightUrl);await page.setContent('<video width="300" height="400"></video>'+notice('<a href="/reel/999">View reel</a>'));
  assert.deepEqual(await page.evaluate(dom,{...lightArgs,discoverPublishedReel:true}),{blocked:false,current_reel_url:lightUrl,reel_video_visible:true,reel_video_count:1},'Current Reel verification does not discover a different notice link');
  await page.goto('https://www.facebook.com/login');await page.setContent('<main><video width="300" height="400"></video></main>');
  assert.deepEqual(await page.evaluate(dom,lightArgs),{blocked:true},'Light reconciliation cannot bypass login route');
  await page.goto(lightUrl);await page.setContent('<main><video width="300" height="400"></video><input name="pass" type="password"></main>');
  assert.deepEqual(await page.evaluate(dom,lightArgs),{blocked:true},'Light reconciliation cannot bypass login field');
  await page.setContent('<main><h1>Confirm your identity</h1><input type="password" name="pass" value="fixture-private-password"></main>');
  data=await page.evaluate(dom,args);assert.deepEqual(data,{blocked:true});assert.equal(JSON.stringify(data).includes('fixture-private'),false);
  assert.deepEqual(menuFailures,[],'App-menu fixture failures');
  console.log('PASS: offline French Menu/Créer/Reel scope, accessible labels, verified actor/banner opener, duplicate targets and strict anchors; composer, hidden/feed/regular-post/overlay/secret exclusions and publication evidence. No live publishing.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
