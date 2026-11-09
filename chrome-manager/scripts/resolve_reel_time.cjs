// Resolve a user's wall-clock time with the installed Node/ICU timezone data.
// No filesystem paths, credentials, video data, browser state or network calls.
const fs=require('node:fs');
function fail(code){process.stdout.write(JSON.stringify({error:code}));process.exitCode=1;}
try{
 const input=JSON.parse(fs.readFileSync(0,'utf8'));
 const timeZone=input.timezone||'UTC';
 const formatter=new Intl.DateTimeFormat('en-CA',{timeZone,hourCycle:'h23',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});
 if(input.validate_only){process.stdout.write(JSON.stringify({timezone:formatter.resolvedOptions().timeZone}));}
 else{
  const match=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(input.local||'');
  if(!match)throw new Error('invalid_local');
  const values=match.slice(1).map(value=>value===undefined?0:Number(value)),[year,month,day,hour,minute,second]=values;
  if(year<2000||year>2100||month<1||month>12||hour>23||minute>59||second>59)throw new Error('invalid_local');
  const naive=Date.UTC(year,month-1,day,hour,minute,second);
  const check=new Date(naive);
  if(check.getUTCFullYear()!==year||check.getUTCMonth()!==month-1||check.getUTCDate()!==day)throw new Error('invalid_local');
  const parts=stamp=>Object.fromEntries(formatter.formatToParts(new Date(stamp)).filter(p=>p.type!=='literal').map(p=>[p.type,Number(p.value)]));
  const offsets=new Set();
  for(const shift of [-36,-24,-12,0,12,24,36]){
   const stamp=naive+shift*3600000,p=parts(stamp);
   offsets.add(Date.UTC(p.year,p.month-1,p.day,p.hour,p.minute,p.second)-stamp);
  }
  const candidates=[...offsets].map(offset=>naive-offset).filter(stamp=>{
   const p=parts(stamp);return p.year===year&&p.month===month&&p.day===day&&p.hour===hour&&p.minute===minute&&p.second===second;
  }).sort((a,b)=>a-b);
  if(!candidates.length)throw new Error('nonexistent_local');
  if(candidates.length>1&&input.fold!==0&&input.fold!==1)throw new Error('ambiguous_local');
  process.stdout.write(JSON.stringify({scheduled_at:new Date(candidates[input.fold===1?candidates.length-1:0]).toISOString(),timezone:formatter.resolvedOptions().timeZone}));
 }
}catch(error){fail(['invalid_local','nonexistent_local','ambiguous_local'].includes(error.message)?error.message:'invalid_timezone');}
