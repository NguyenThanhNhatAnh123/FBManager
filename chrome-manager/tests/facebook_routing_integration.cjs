'use strict';
// Owned loopback fixtures only; no Facebook, Outlook or personal account is contacted.
const assert=require('node:assert/strict'),http=require('node:http'),net=require('node:net');
const fs=require('node:fs'),path=require('node:path'),readline=require('node:readline');
const {spawn,spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),servers=[],children=[],sockets=new Set();
const proof={localIntegration:false,realGoogleChrome:false,facebookProxyFailClosed:false,outlookRuleDirect:false};
let proxyHttp=0,proxyConnect=0,proxyWs=0,directHttp=0,directWs=0;
const expected='Basic '+Buffer.from('fixture-user:fixture-secret').toString('base64');
function listen(server){return new Promise((resolve,reject)=>{server.on('connection',s=>{sockets.add(s);s.on('close',()=>sockets.delete(s));s.on('error',()=>{})});server.once('error',reject);server.listen(0,'127.0.0.1',()=>{servers.push(server);resolve(server.address().port)})})}
async function collect(stream){const parts=[];for await(const part of stream)parts.push(part);return Buffer.concat(parts).toString()}
function websocket(socket){socket.write('HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n');socket.pipe(socket)}
async function bridge(port,{wrong=false,deny=false,profile,routing='facebook_only'}={}){
 const child=spawn(process.execPath,[path.join(root,'scripts/proxy_bridge.cjs')],{stdio:['pipe','pipe','pipe'],windowsHide:true});children.push(child);
 let resolveReady,rejectReady,stderr='';const errors=[];
 const ready=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject});
 child.once('exit',()=>rejectReady(new Error('Fixture adapter exited before ready')));child.stderr.on('data',part=>stderr+=part);
 readline.createInterface({input:child.stdout}).on('line',line=>{
  const message=JSON.parse(line);
  if(message.type==='ready')resolveReady(message.port);
  if(message.type==='error')errors.push(message.code);
  if(message.type==='authorize'){
   let allow=!deny;
   if(profile){
    const check=spawnSync(path.join(root,'.venv/Scripts/python.exe'),[path.join(root,'tests/proxy_bridge_owner.py'),profile,String(message.client_port),String(message.server_port)],{encoding:'utf8',timeout:4000,windowsHide:true});
    allow=check.status===0&&check.stdout.trim()==='allowed';
   }
   child.stdin.write(JSON.stringify({type:'authorize',id:message.id,allow})+'\n');
  }
 });
 child.stdin.write(JSON.stringify({protocol:'http',host:'127.0.0.1',port,username:'fixture-user',password:wrong?'wrong':'fixture-secret',routing})+'\n');
 const localPort=await Promise.race([ready,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Fixture adapter startup timeout')),10000).unref())]);
 return {port:localPort,errors,async close(){if(child.exitCode===null){child.stdin.end('{"type":"close"}\n');await new Promise((resolve,reject)=>{child.once('exit',resolve);setTimeout(()=>reject(new Error('Fixture adapter close timeout')),6000).unref()})}assert.equal(stderr,'')}};
}
function request(port,url,{method='GET',body,headers={}}={}){return new Promise((resolve,reject)=>{
 const req=http.request({host:'127.0.0.1',port,path:url,method,headers,agent:false},async response=>{try{resolve({status:response.statusCode,headers:response.headers,body:await collect(response)})}catch(error){reject(error)}});
 req.on('error',reject);req.setTimeout(10000,()=>req.destroy(new Error('Fixture request timeout')));if(body){req.write(body.slice(0,4));req.write(body.slice(4))}req.end();
})}
function tunnel(port,target){return new Promise((resolve,reject)=>{
 const req=http.request({host:'127.0.0.1',port,path:target,method:'CONNECT',agent:false});req.on('error',reject);req.setTimeout(10000,()=>req.destroy(new Error('Fixture tunnel timeout')));
 req.on('connect',(response,socket,head)=>resolve({response,socket,head}));req.end();
})}
function upgrade(port,url){return new Promise((resolve,reject)=>{
 const req=http.request({host:'127.0.0.1',port,path:url,headers:{connection:'Upgrade',upgrade:'websocket'},agent:false});req.on('error',reject);req.setTimeout(10000,()=>req.destroy(new Error('Fixture websocket timeout')));
 req.on('upgrade',(response,socket)=>resolve({response,socket}));req.on('response',res=>{res.resume();reject(new Error('Expected fixture websocket upgrade'))});req.end();
})}
async function echo(socket){const received=new Promise((resolve,reject)=>{socket.once('data',data=>resolve(data.toString()));socket.once('error',reject);socket.setTimeout(5000,()=>reject(new Error('Fixture echo timeout')))});socket.write('opaque fixture bytes');assert.equal(await received,'opaque fixture bytes');socket.destroy()}
(async()=>{
 const origin=http.createServer(async(req,res)=>{
  directHttp++;assert.equal(req.headers['proxy-authorization'],undefined);assert.equal(req.headers['proxy-connection'],undefined);
  const body=await collect(req);res.writeHead(200,{'Content-Type':'text/html'});res.end(body||'<title>Owned direct route</title>direct');
 });origin.on('upgrade',(_,socket)=>{directWs++;websocket(socket)});
 const originPort=await listen(origin),echoPort=await listen(net.createServer(socket=>socket.pipe(socket)));
 const upstream=http.createServer((req,res)=>{
  proxyHttp++;if(req.headers['proxy-authorization']!==expected){res.writeHead(407,{'Proxy-Authenticate':'Basic realm="fixture"'});res.end();return}
  const host=new URL(req.url).hostname;assert.ok(host==='www.facebook.com'||host==='static.xx.fbcdn.net'||host==='127.0.0.1');
  res.writeHead(200,{'Content-Type':'text/plain'});res.end('assigned upstream proxy');
 });
 upstream.on('connect',(req,client,head)=>{
  proxyConnect++;if(req.headers['proxy-authorization']!==expected){client.end('HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="fixture"\r\nContent-Length: 0\r\n\r\n');return}
  assert.equal(req.url,'www.facebook.com:443');
  const remote=net.connect(echoPort,'127.0.0.1',()=>{client.write('HTTP/1.1 200 Connection Established\r\n\r\n');if(head.length)remote.write(head);client.pipe(remote);remote.pipe(client)});
  remote.on('error',()=>client.destroy());remote.on('close',()=>client.destroy());client.on('close',()=>remote.destroy());
 });
 upstream.on('upgrade',(req,socket)=>{proxyWs++;assert.equal(req.headers['proxy-authorization'],expected);assert.equal(new URL(req.url).hostname,'www.facebook.com');websocket(socket)});
 const upstreamPort=await listen(upstream),adapter=await bridge(upstreamPort),originUrl='http://127.0.0.1:'+originPort;
 assert.equal((await request(adapter.port,'http://www.facebook.com/fixture')).body,'assigned upstream proxy');
 assert.equal((await request(adapter.port,'http://static.xx.fbcdn.net/fixture')).body,'assigned upstream proxy');
 const proxyBefore=proxyHttp;
 const direct=await request(adapter.port,originUrl+'/fixture',{method:'POST',body:'chunked direct body',headers:{'Proxy-Authorization':'fixture-untrusted-header','Proxy-Connection':'keep-alive'}});
 assert.equal(direct.status,200);assert.equal(direct.body,'chunked direct body');assert.equal(proxyHttp,proxyBefore);
 const proxiedTunnel=await tunnel(adapter.port,'www.facebook.com:443');assert.equal(proxiedTunnel.response.statusCode,200);await echo(proxiedTunnel.socket);
 const connectsBefore=proxyConnect,directTunnel=await tunnel(adapter.port,'127.0.0.1:'+echoPort);assert.equal(directTunnel.response.statusCode,200);await echo(directTunnel.socket);assert.equal(proxyConnect,connectsBefore);
 const proxyUpgrade=await upgrade(adapter.port,'ws://www.facebook.com/fixture');assert.equal(proxyUpgrade.response.statusCode,101);await echo(proxyUpgrade.socket);
 const directUpgrade=await upgrade(adapter.port,'ws://127.0.0.1:'+originPort+'/fixture');assert.equal(directUpgrade.response.statusCode,101);await echo(directUpgrade.socket);assert.equal(proxyWs,1);assert.equal(directWs,1);
 const bad=await bridge(upstreamPort,{wrong:true}),directBefore=directHttp;
 const rejected=await request(bad.port,'http://www.facebook.com/fixture');assert.equal(rejected.status,502);assert.equal(rejected.headers['proxy-authenticate'],undefined);assert.equal(directHttp,directBefore);
 const rejectedTunnel=await tunnel(bad.port,'www.facebook.com:443');assert.equal(rejectedTunnel.response.statusCode,502);rejectedTunnel.socket.destroy();assert.equal(directHttp,directBefore);
 assert.equal((await request(bad.port,originUrl+'/fixture')).status,200);proof.facebookProxyFailClosed=true;
 const denied=await bridge(upstreamPort,{deny:true}),before=[proxyHttp,directHttp];
 assert.equal((await request(denied.port,originUrl+'/')).status,403);assert.equal((await request(denied.port,'http://www.facebook.com/fixture')).status,403);assert.deepEqual([proxyHttp,directHttp],before);
 const all=await bridge(upstreamPort,{routing:'all'});assert.equal((await request(all.port,originUrl+'/fixture')).body,'assigned upstream proxy');
 for(const item of [adapter,bad,denied,all])await item.close();
 const {usesUpstream}=require('../scripts/proxy_routing.cjs');proof.outlookRuleDirect=!usesUpstream('login.live.com','facebook_only')&&!usesUpstream('outlook.live.com','facebook_only');proof.localIntegration=true;
 console.log('PASS: Facebook HTTP/CONNECT/WebSocket uses assigned proxy; other HTTP/CONNECT/WebSocket goes direct; credentials stripped; failures stay blocked; unauthorized clients denied; legacy mode preserved.');
 if(process.argv.includes('--browser')){
  const {chromium}=require('../frontend/node_modules/playwright');fs.mkdirSync(path.join(root,'.runtime'),{recursive:true});
  const directory=fs.mkdtempSync(path.join(root,'.runtime','facebook-routing-test-')),owned=await bridge(upstreamPort,{profile:directory});
  assert.equal((await request(owned.port,originUrl+'/')).status,403);
  const context=await chromium.launchPersistentContext(directory,{channel:'chrome',headless:true,proxy:{server:'http://127.0.0.1:'+owned.port},args:['--proxy-bypass-list=<-loopback>'],timeout:20000});
  try{const page=context.pages()[0]||await context.newPage();const response=await page.goto(originUrl+'/',{waitUntil:'domcontentloaded',timeout:20000});assert.equal(response.status(),200);assert.equal(await page.title(),'Owned direct route');proof.realGoogleChrome=true;console.log('PASS: installed Google Chrome uses direct route through production profile ownership verification.');}finally{await context.close();await owned.close()}
 }
})().catch(error=>{proof.error=error.code||'FIXTURE_ASSERTION_FAILED';console.error(error.message);process.exitCode=1}).finally(()=>{
 fs.mkdirSync(path.join(root,'logs'),{recursive:true});fs.writeFileSync(path.join(root,'logs/facebook-routing-validation.json'),JSON.stringify(proof,null,2));
 for(const child of children)if(child.exitCode===null)child.kill();for(const socket of sockets)socket.destroy();for(const server of servers)server.close();
});
