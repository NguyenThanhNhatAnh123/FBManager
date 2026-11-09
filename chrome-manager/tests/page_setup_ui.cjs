// Isolated rendered media fixtures only. No Facebook, saved profile or network.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../backend/app/services/page_setup.py'),'utf8');
const media=new Function('return ('+source.match(/MEDIA_READ=r'''([\s\S]*?)'''/)[1]+')')();
const picture='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="blue"/></svg>');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({offline:true});const page=await context.newPage();
  await page.setContent('<main><h1>Fixture Page</h1><img alt="Fixture Page profile picture" width="170" height="170" src="'+picture+'"><img alt="Cover photo" width="800" height="250" src="'+picture+'#cover"><div role="article"><img alt="Profile picture" width="1000" height="1000" src="'+picture+'#post"></div><div role="dialog"><img alt="Profile picture" width="1200" height="1200" src="'+picture+'#preview"></div><img alt="Profile picture" style="display:none" width="1500" height="1500" src="'+picture+'#hidden"></main>');
  await page.locator('img').first().waitFor();
  await page.waitForFunction(()=>[...document.images].every(image=>image.complete));
  assert.equal(await page.evaluate(media,{kind:'avatar'}),picture,'Only saved visible header profile image counts, excluding posts/preview/hidden media');
  assert.equal(await page.evaluate(media,{kind:'cover'}),picture+'#cover');
  await page.setContent('<main><h1>Fixture Page</h1><svg role="img" aria-label="Ảnh đại diện" width="180" height="180"><image width="180" height="180" href="'+picture+'#svg"></image></svg><img alt="Ảnh bìa" width="800" height="250" src="'+picture+'#vi-cover"></main>');
  await page.waitForFunction(()=>[...document.images].every(image=>image.complete));
  assert.equal(await page.evaluate(media,{kind:'avatar'}),picture+'#svg','SVG Facebook header image and Vietnamese label are supported');
  assert.equal(await page.evaluate(media,{kind:'cover'}),picture+'#vi-cover');
  await page.setContent('<main><img alt="Unrelated advertisement" width="800" height="800" src="'+picture+'"><img alt="Profile picture" width="10" height="10" src="'+picture+'#nav"><img alt="Cover photo" width="800" height="300" src="data:image/png;base64,broken"></main>');
  await page.waitForFunction(()=>[...document.images].every(image=>image.complete));
  assert.equal(await page.evaluate(media,{kind:'avatar'}),'','Unrelated media and small navigation avatars never verify a Page photo');
  assert.equal(await page.evaluate(media,{kind:'cover'}),'','Failed image decoding never verifies a saved cover');
  console.log('PASS: Page setup media readback finds loaded visible header avatar/cover, SVG and Vietnamese labels; excludes dialog previews, posts, hidden/small/unrelated and undecodable images. Offline fixtures only.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
