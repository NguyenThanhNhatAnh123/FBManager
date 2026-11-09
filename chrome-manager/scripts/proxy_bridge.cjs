// Private per-profile proxy adapter. Configuration arrives via stdin, never argv.
// Only stdout JSON control messages are emitted; credentials/URLs/headers are not logged.
'use strict';
const http=require('node:http'),https=require('node:https'),net=require('node:net');
const readline=require('node:readline');
const {usesUpstream}=require('./proxy_routing.cjs');
const sockets=new Set(),pending=new Map(),reported=new Set();
let config,server,sequence=0,closing=false;
const emit=value=>process.stdout.write(JSON.stringify(value)+'\n');
function issue(code){if(!reported.has(code)){reported.add(code);emit({type:'error',code})}}
function stop(){if(closing)return;closing=true;for(const item of pending.values()){clearTimeout(item.timer);item.resolve(false)}pending.clear();for(const socket of sockets)socket.destroy();if(server)server.close(()=>process.exit(0));else process.exit(0);setTimeout(()=>process.exit(0),1000).unref()}
function track(socket){sockets.add(socket);socket.once('close',()=>sockets.delete(socket));socket.on('error',()=>{});return socket}
function authorized(socket){
 if(socket.cmAuthorization)return socket.cmAuthorization;
 const id=++sequence;
 socket.cmAuthorization=new Promise(resolve=>{
  const timer=setTimeout(()=>{pending.delete(id);issue('LOCAL_DENIED');resolve(false)},5000);timer.unref();
  pending.set(id,{resolve,timer});
  emit({type:'authorize',id,client_port:socket.remotePort,server_port:socket.localPort});
 });return socket.cmAuthorization;
}
function headers(input,upgrade=false){
 const blocked=new Set(['proxy-authorization','proxy-authenticate','proxy-connection','keep-alive','connection','transfer-encoding','te','trailer']);
 for(const item of String(input.connection||'').split(','))if(item.trim())blocked.add(item.trim().toLowerCase());
 const result={};for(const [name,value] of Object.entries(input))if(!blocked.has(name.toLowerCase())&&value!==undefined)result[name]=value;
 if(upgrade){result.connection='Upgrade';result.upgrade='websocket'}
 return result;
}
function proxyAuth(){return 'Basic '+Buffer.from(config.username+':'+config.password,'utf8').toString('base64')}
function transport(){return config.protocol==='https'?https:http}
function options(method,path,input){return {host:config.host,port:config.port,method,path,headers:{...input,...(config.username?{'Proxy-Authorization':proxyAuth()}:{})},agent:false,timeout:20000,...(config.protocol==='https'&&!net.isIP(config.host)?{servername:config.host}:{})}}
function directOptions(method,target,input){return {host:target.hostname.replace(/^\[|\]$/g,''),port:Number(target.port)||80,method,path:target.pathname+target.search,headers:input,agent:false,timeout:20000}}
function targetUrl(raw){const value=new URL(raw);if(!['http:','ws:'].includes(value.protocol)||!value.hostname||value.username||value.password)throw new Error('invalid');return value}
function sendError(response,status=502){if(!response.headersSent)response.writeHead(status,{'Content-Type':'text/plain','Connection':'close'});response.end(status===403?'Local proxy access denied.':'The selected connection route could not be completed.');}
function socketError(socket,status=502){if(!socket.destroyed)socket.end(`HTTP/1.1 ${status} ${status===403?'Forbidden':'Bad Gateway'}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`)}
function upstreamError(error){issue(error&&error.code==='ETIMEDOUT'?'UPSTREAM_TIMEOUT':error&&String(error.code).includes('CERT')?'UPSTREAM_TLS':'UPSTREAM_CONNECT')}
function directError(error){issue(error&&error.code==='ETIMEDOUT'?'DIRECT_TIMEOUT':'DIRECT_CONNECT')}
async function forward(request,response){
 if(!await authorized(request.socket)){sendError(response,403);return}
 let target;try{target=targetUrl(request.url)}catch{sendError(response,400);return}
 const outgoing=headers(request.headers);outgoing.host=target.host;outgoing.connection='close';
 const destination='http://'+target.host+target.pathname+target.search;
 const proxied=usesUpstream(target.hostname,config.routing);
 const upstream=(proxied?transport():http).request(proxied?options(request.method,destination,outgoing):directOptions(request.method,target,outgoing),remote=>{
  if(proxied&&remote.statusCode===407){issue('UPSTREAM_AUTH');remote.resume();sendError(response);return}
  response.writeHead(remote.statusCode,headers(remote.headers));remote.pipe(response);
 });
 upstream.on('socket',track);upstream.on('timeout',()=>{issue(proxied?'UPSTREAM_TIMEOUT':'DIRECT_TIMEOUT');upstream.destroy()});
 upstream.on('error',error=>{(proxied?upstreamError:directError)(error);sendError(response)});
 request.once('aborted',()=>upstream.destroy());response.once('close',()=>{if(!response.writableFinished)upstream.destroy()});
 request.pipe(upstream);
}
function authority(raw){
 if(typeof raw!=='string'||raw.length>1024||/[\s/?#@]/.test(raw))throw new Error('invalid');
 const match=/^(\[[^\]]+\]|[^:]+):(\d+)$/.exec(raw);
 if(!match||Number(match[2])<1||Number(match[2])>65535)throw new Error('invalid');
 const value=new URL('http://'+raw);if(!value.hostname)throw new Error('invalid');
 return {address:value.hostname+':'+Number(match[2]),hostname:value.hostname,port:Number(match[2])};
}
function directTunnel(target,client,head){
 let connected=false;
 const remote=track(net.connect({host:target.hostname.replace(/^\[|\]$/g,''),port:target.port}));
 remote.setTimeout(20000,()=>{issue('DIRECT_TIMEOUT');remote.destroy();if(!connected)socketError(client);else client.destroy()});
 remote.once('connect',()=>{
  if(client.destroyed){remote.destroy();return}
  connected=true;remote.setTimeout(0);client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
  if(head.length)remote.write(head);client.pipe(remote);remote.pipe(client);
 });
 remote.on('error',error=>{directError(error);if(connected)client.destroy();else socketError(client)});
 client.once('close',()=>remote.destroy());remote.once('close',()=>{if(connected)client.destroy()});
}
async function tunnel(request,client,head){
 track(client);if(!await authorized(client)){socketError(client,403);return}
 let target;try{target=authority(request.url)}catch{socketError(client,400);return}
 if(!usesUpstream(target.hostname,config.routing)){directTunnel(target,client,head);return}
 const upstream=transport().request(options('CONNECT',target.address,{host:target.address}));
 upstream.on('socket',track);
 upstream.on('connect',(response,remote,remoteHead)=>{
  track(remote);
  if(response.statusCode!==200){if(response.statusCode===407)issue('UPSTREAM_AUTH');else issue('UPSTREAM_TUNNEL');remote.destroy();socketError(client);return}
  upstream.setTimeout(0);remote.setTimeout(0);
  client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
  if(head.length)remote.write(head);if(remoteHead.length)client.write(remoteHead);
  client.pipe(remote);remote.pipe(client);
  client.once('close',()=>remote.destroy());remote.once('close',()=>client.destroy());
 });
 upstream.on('response',response=>{response.resume();if(response.statusCode===407)issue('UPSTREAM_AUTH');else issue('UPSTREAM_TUNNEL');socketError(client)});
 upstream.on('timeout',()=>{issue('UPSTREAM_TIMEOUT');upstream.destroy()});
 upstream.on('error',error=>{upstreamError(error);socketError(client)});
 client.once('close',()=>upstream.destroy());upstream.end();
}
async function upgrade(request,client,head){
 track(client);if(!await authorized(client)){socketError(client,403);return}
 let target;try{target=targetUrl(request.url);if(String(request.headers.upgrade).toLowerCase()!=='websocket')throw new Error('invalid')}catch{socketError(client,400);return}
 const outgoing=headers(request.headers,true);outgoing.host=target.host;
 const proxied=usesUpstream(target.hostname,config.routing);
 const upstream=(proxied?transport():http).request(proxied?options(request.method,'http://'+target.host+target.pathname+target.search,outgoing):directOptions(request.method,target,outgoing));
 upstream.on('socket',track);
 upstream.on('upgrade',(response,remote,remoteHead)=>{
  track(remote);upstream.setTimeout(0);remote.setTimeout(0);
  const clean=headers(response.headers,true);let opening=`HTTP/1.1 ${response.statusCode} Switching Protocols\r\n`;
  for(const [name,value] of Object.entries(clean))opening+=name+': '+value+'\r\n';
  client.write(opening+'\r\n');if(head.length)remote.write(head);if(remoteHead.length)client.write(remoteHead);
  client.pipe(remote);remote.pipe(client);client.once('close',()=>remote.destroy());remote.once('close',()=>client.destroy());
 });
 upstream.on('response',response=>{response.resume();if(proxied&&response.statusCode===407)issue('UPSTREAM_AUTH');else issue(proxied?'UPSTREAM_TUNNEL':'DIRECT_CONNECT');socketError(client)});
 upstream.on('timeout',()=>{issue(proxied?'UPSTREAM_TIMEOUT':'DIRECT_TIMEOUT');upstream.destroy()});upstream.on('error',error=>{(proxied?upstreamError:directError)(error);socketError(client)});
 client.once('close',()=>upstream.destroy());upstream.end();
}
function start(value){
 if(config||!value||!['http','https'].includes(value.protocol)||typeof value.host!=='string'||!value.host||!Number.isInteger(value.port)||value.port<1||value.port>65535||typeof value.username!=='string'||typeof value.password!=='string'){issue('CONFIGURATION');stop();return}
 if(value.routing!==undefined&&!['all','facebook_only'].includes(value.routing)){issue('CONFIGURATION');stop();return}
 config={...value,routing:value.routing||'all'};
 server=http.createServer({maxHeaderSize:65536},(request,response)=>forward(request,response).catch(()=>sendError(response)));
 server.headersTimeout=15000;server.requestTimeout=300000;server.maxConnections=256;
 server.on('connection',socket=>{track(socket);authorized(socket)});
 server.on('connect',(request,socket,head)=>tunnel(request,socket,head).catch(()=>socketError(socket)));
 server.on('upgrade',(request,socket,head)=>upgrade(request,socket,head).catch(()=>socketError(socket)));
 server.on('clientError',(_,socket)=>socketError(socket,400));server.on('error',()=>{issue('LOCAL_LISTEN');stop()});
 server.listen(0,'127.0.0.1',()=>emit({type:'ready',port:server.address().port}));
}
const input=readline.createInterface({input:process.stdin});
input.on('line',line=>{try{const value=JSON.parse(line);if(!config){start(value);return}if(value.type==='authorize'){const item=pending.get(value.id);if(item){pending.delete(value.id);clearTimeout(item.timer);if(value.allow!==true)issue('LOCAL_DENIED');item.resolve(value.allow===true)}}else if(value.type==='close')stop()}catch{issue('CONFIGURATION');stop()}});
input.on('close',stop);process.on('SIGTERM',stop);process.on('uncaughtException',()=>{issue('INTERNAL');stop()});process.on('unhandledRejection',()=>{issue('INTERNAL');stop()});
