'use strict';
// Integration fixtures are real local servers. No fixture is used by the product.
const assert=require('node:assert/strict');
const http=require('node:http'),net=require('node:net'),readline=require('node:readline');
const fs=require('node:fs'),path=require('node:path');
const {spawn,spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const resources=[],children=[],connections=new Set();
const fixtures={requests:0,connects:0,failures:0,externalBlocked:0};
const proof={createdAt:new Date().toISOString(),fixtureIntegration:false,realChromium:false,profileOwnership:false};
const secret='fixture-account:fixture-password';
const expected='Basic '+Buffer.from(secret).toString('base64');
function listen(server){return new Promise((resolve,reject)=>{server.on('connection',s=>{connections.add(s);s.on('close',()=>connections.delete(s));s.on('error',()=>{})});server.once('error',reject);server.listen(0,'127.0.0.1',()=>{resources.push(server);resolve(server.address().port)})})}
function collect(stream){return new Promise((resolve,reject)=>{const chunks=[];stream.on('data',x=>chunks.push(x));stream.on('end',()=>resolve(Buffer.concat(chunks).toString()));stream.on('error',reject)})}
async function bridge(upstreamPort,{deny=false,profile,wrong=false}={}){
 const child=spawn(process.execPath,[path.join(root,'scripts','proxy_bridge.cjs')],{stdio:['pipe','pipe','pipe'],windowsHide:true});
 children.push(child);let ready,readyReject,stderr='';const errors=[];
 const initialized=new Promise((resolve,reject)=>{ready=resolve;readyReject=reject});
 child.stderr.on('data',chunk=>stderr+=chunk);child.once('exit',code=>readyReject(new Error('Adapter exited: '+code)));
 const lines=readline.createInterface({input:child.stdout});
 lines.on('line',line=>{
  const message=JSON.parse(line);
  if(message.type==='ready')ready(message.port);
  else if(message.type==='error')errors.push(message.code);
  else if(message.type==='authorize'){
   let allow=!deny;
   if(profile){
    const probe=spawnSync(path.join(root,'.venv','Scripts','python.exe'),[path.join(root,'tests','proxy_bridge_owner.py'),profile,String(message.client_port),String(message.server_port)],{encoding:'utf8',windowsHide:true,timeout:4000});
    allow=probe.status===0&&probe.stdout.trim()==='allowed';
   }
   child.stdin.write(JSON.stringify({type:'authorize',id:message.id,allow})+'\n');
  }
 });
 child.stdin.write(JSON.stringify({protocol:'http',host:'127.0.0.1',port:upstreamPort,username:'fixture-account',password:wrong?'wrong':'fixture-password'})+'\n');
 const port=await Promise.race([initialized,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Adapter startup timeout')),10000).unref())]);
 return {port,errors,async close(){if(child.exitCode===null){child.stdin.end('{"type":"close"}\n');await new Promise((resolve,reject)=>{child.once('exit',resolve);setTimeout(()=>{child.kill();reject(new Error('Adapter shutdown timeout'))},6000).unref()})}assert.equal(stderr,'')}};
}
function request(port,url,method='GET',body){return new Promise((resolve,reject)=>{
 const req=http.request({host:'127.0.0.1',port,path:url,method,agent:false},async res=>{try{resolve({status:res.statusCode,headers:res.headers,body:await collect(res)})}catch(e){reject(e)}});
 req.on('error',reject);req.setTimeout(10000,()=>req.destroy(new Error('Request timeout')));
 if(body){req.write(body.slice(0,4));req.write(body.slice(4))}req.end();
})}
async function connect(port,target){return await new Promise((resolve,reject)=>{
 const req=http.request({host:'127.0.0.1',port,path:target,method:'CONNECT',agent:false});
 req.on('error',reject);req.setTimeout(10000,()=>req.destroy(new Error('Tunnel timeout')));
 req.on('connect',(res,socket,head)=>resolve({res,socket,head}));req.on('response',res=>{res.resume();reject(new Error('Unexpected ordinary response'))});req.end();
})}
(async()=>{
 const originPort=await listen(http.createServer(async(req,res)=>{
  assert.equal(req.headers['proxy-authorization'],undefined,'Credentials must never reach the origin');
  const body=await collect(req);
  if(req.url==='/origin-auth'){res.writeHead(401,{'WWW-Authenticate':'Basic realm="owned-origin"'});res.end();return}
  res.writeHead(200,{'Content-Type':'text/html'});res.end(body||'<title>Owned proxy fixture</title><p>real connection</p>');
 }));
 const echoPort=await listen(net.createServer(socket=>socket.pipe(socket)));
 const upstream=http.createServer((req,res)=>{
  fixtures.requests++;
  if(req.headers['proxy-authorization']!==expected){fixtures.failures++;res.writeHead(407,{'Proxy-Authenticate':'Basic realm="fixture"'});res.end();return}
  const target=new URL(req.url);if(target.hostname!=='127.0.0.1'||Number(target.port)!==originPort){fixtures.externalBlocked++;res.writeHead(502);res.end();return}
  const headers={...req.headers};delete headers['proxy-authorization'];delete headers['proxy-connection'];
  const out=http.request({host:'127.0.0.1',port:originPort,path:target.pathname,method:req.method,headers,agent:false},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res)});
  out.on('error',()=>{res.writeHead(502);res.end()});req.pipe(out);
 });
 upstream.on('connect',(req,client,head)=>{
  fixtures.connects++;
  if(req.headers['proxy-authorization']!==expected){fixtures.failures++;client.end('HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="fixture"\r\nContent-Length: 0\r\n\r\n');return}
  if(req.url==='fixture.invalid:80'){client.end('HTTP/1.1 200 Connection Established\r\n\r\nport80');return}
  if(req.url!=='127.0.0.1:'+echoPort){fixtures.externalBlocked++;client.end('HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n');return}
  const remote=net.connect(echoPort,'127.0.0.1',()=>{client.write('HTTP/1.1 200 Connection Established\r\n\r\n');if(head.length)remote.write(head);client.pipe(remote);remote.pipe(client)});
  remote.on('error',()=>client.destroy());remote.on('close',()=>client.destroy());client.on('close',()=>remote.destroy());
 });
 const proxyPort=await listen(upstream),local=await bridge(proxyPort);
 const origin='http://127.0.0.1:'+originPort;
 assert.equal((await request(local.port,origin+'/')).status,200);
 const post=await request(local.port,origin+'/post','POST','chunked form body');assert.equal(post.body,'chunked form body');
 const originAuth=await request(local.port,origin+'/origin-auth');assert.equal(originAuth.status,401);assert.ok(originAuth.headers['www-authenticate']);
 const tunnel=await connect(local.port,'127.0.0.1:'+echoPort);assert.equal(tunnel.res.statusCode,200);
 tunnel.socket.write('tunnel payload');assert.equal(await new Promise(resolve=>tunnel.socket.once('data',b=>resolve(b.toString()))),'tunnel payload');tunnel.socket.destroy();
 const port80=await connect(local.port,'fixture.invalid:80');assert.equal(port80.res.statusCode,200);port80.socket.destroy();
 assert.equal(fixtures.failures,0);assert.deepEqual(local.errors,[]);
 const blocked=await bridge(proxyPort,{deny:true}),before=fixtures.requests;
 assert.equal((await request(blocked.port,origin+'/')).status,403);assert.equal(fixtures.requests,before);
 const wrong=await bridge(proxyPort,{wrong:true});
 const refusal=await request(wrong.port,origin+'/');assert.equal(refusal.status,502);assert.equal(refusal.headers['proxy-authenticate'],undefined);
 const refusedTunnel=await connect(wrong.port,'127.0.0.1:'+echoPort);assert.equal(refusedTunnel.res.statusCode,502);assert.equal(refusedTunnel.res.headers['proxy-authenticate'],undefined);refusedTunnel.socket.destroy();
 assert.ok(wrong.errors.includes('UPSTREAM_AUTH'));
 await wrong.close();await blocked.close();await local.close();
 proof.fixtureIntegration=true;
 console.log('PASS: actual HTTP/chunked POST/CONNECT/default port, upstream auth, denied local clients, sanitized 407, shutdown.');
 if(process.argv.includes('--browser')||process.env.CM_TEST_CHROMIUM_PATH){
  if(!process.env.CM_TEST_CHROMIUM_PATH){
   const installed=fs.readdirSync(path.join(root,'.browsers')).filter(name=>/^chromium-\d+$/.test(name)).sort((a,b)=>Number(b.split('-')[1])-Number(a.split('-')[1]));
   process.env.CM_TEST_CHROMIUM_PATH=installed.map(name=>path.join(root,'.browsers',name,'chrome-win64','chrome.exe')).find(file=>fs.existsSync(file));
   assert.ok(process.env.CM_TEST_CHROMIUM_PATH,'No private Chromium installed; run the existing Authenticator launcher first.');
  }
  const {chromium}=require('../frontend/node_modules/playwright');
  const directory=fs.mkdtempSync(path.join(root,'.runtime','proxy-auth-test-'));
  const ownerBridge=await bridge(proxyPort,{profile:directory});
  assert.equal((await request(ownerBridge.port,origin+'/')).status,403,'Non-Chrome clients must be denied by production ownership checker');
  const context=await chromium.launchPersistentContext(directory,{executablePath:process.env.CM_TEST_CHROMIUM_PATH,headless:true,proxy:{server:'http://127.0.0.1:'+ownerBridge.port},args:['--proxy-bypass-list=<-loopback>'],timeout:20000});
  try{
   const page=context.pages()[0]||await context.newPage();
   const response=await page.goto(origin+'/',{waitUntil:'domcontentloaded',timeout:20000});assert.equal(response.status(),200);assert.equal(await page.title(),'Owned proxy fixture');
   await page.reload({waitUntil:'domcontentloaded',timeout:10000});assert.equal(await page.title(),'Owned proxy fixture');
   proof.realChromium=true;proof.profileOwnership=true;
   console.log('PASS: real Chromium loaded/reloaded an owned website through saved upstream auth; production Windows process/profile ownership verified.');
  }finally{await context.close();await ownerBridge.close()}
 }
})().catch(error=>{console.error(error.message);proof.error=error.code||error.message;process.exitCode=1}).finally(async()=>{
 fs.mkdirSync(path.join(root,'logs'),{recursive:true});fs.writeFileSync(path.join(root,'logs','proxy-auth-validation.json'),JSON.stringify(proof,null,2));
 for(const child of children)if(child.exitCode===null)child.kill();for(const socket of connections)socket.destroy();for(const server of resources)server.close();
});
