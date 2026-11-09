// Real, offline extension autoload smoke test. Uses fresh temporary profiles only.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium}=require('../frontend/node_modules/playwright');
(async()=>{
 const root=path.resolve(__dirname,'..');
 const plan=JSON.parse(fs.readFileSync(path.join(root,'data','authenticator-plan.json'),'utf8'));
 const bundle=path.resolve(root,plan.bundle);
 const executable=process.env.CM_TEST_CHROMIUM_PATH;
 assert(executable&&fs.existsSync(executable),'Set CM_TEST_CHROMIUM_PATH to an installed Playwright Chromium executable.');
 assert(bundle.startsWith(path.join(root,'.runtime','extensions')+path.sep));
 const output=path.join(root,'.runtime','ui');fs.mkdirSync(output,{recursive:true});
 const directory=fs.mkdtempSync(path.join(root,'.runtime','auth-autoload-test-'));
 const reports=[];let browserVersion='';
 async function launch(name){
  const context=await chromium.launchPersistentContext(path.join(directory,name),{
   executablePath:executable,headless:true,ignoreDefaultArgs:['--disable-extensions'],viewport:{width:850,height:700},
   args:[`--disable-extensions-except=${bundle}`,`--load-extension=${bundle}`,'--proxy-server=http://127.0.0.1:9','--proxy-bypass-list=<-loopback>'],
  });
  try{
   browserVersion=context.browser().version();
   await context.route(/^https?:/,route=>route.abort('blockedbyclient'));
   let worker=context.serviceWorkers().find(worker=>worker.url().startsWith(`chrome-extension://${plan.extension_id}/`));
   if(!worker)worker=await context.waitForEvent('serviceworker',{predicate:worker=>worker.url().startsWith(`chrome-extension://${plan.extension_id}/`),timeout:20000});
   assert(worker.url().startsWith(`chrome-extension://${plan.extension_id}/`));
   const page=await context.newPage();
   await page.goto(`chrome-extension://${plan.extension_id}/view/popup.html`,{waitUntil:'domcontentloaded',timeout:15000});
   assert(page.url().startsWith(`chrome-extension://${plan.extension_id}/`));
   await page.waitForFunction(()=>document.body.innerText.trim().length>0,null,{timeout:15000});
   const text=await page.locator('body').innerText();
   assert(text.trim().length>0,'The official extension UI must render.');
   await page.screenshot({path:path.join(output,`authenticator-auto-${name}.png`),fullPage:true});
   reports.push({profile:name,extensionId:plan.extension_id,serviceWorker:true,popupRendered:true});
  }finally{await context.close()}
 }
 await launch('first');await launch('second');await launch('first');
 fs.writeFileSync(path.join(root,'.runtime','auth-autoload-proof.json'),JSON.stringify({offline:true,temporaryProfiles:true,profileDirectories:2,reopenVerified:true,browserVersion,executable:fs.realpathSync(executable),extensionId:plan.extension_id,bundleVersion:plan.version,payloadManifestHash:plan.files['manifest.json'],reports},null,2));
 console.log('PASS: official Authenticator auto-loaded into 2 independent real Chromium profiles and reopened successfully; no Web Store clicks or account data copies. Test network is blocked.');
})().catch(error=>{console.error(error.message);process.exit(1)});
