/** Display-only calendar values. Scheduling payloads remain ISO wall-clock/UTC. */
export type CalendarGroup = 'day'|'month'|'year'

export function calendarFilter(value:string,mode:'all'|CalendarGroup){
 const expression=mode==='day'?/^\d{4}-\d{2}-\d{2}$/:mode==='month'?/^\d{4}-\d{2}$/:/^\d{4}$/
 if(mode==='all'||!expression.test(value)||Number(value.slice(0,4))<1000)return undefined
 if(mode!=='year'&&(Number(value.slice(5,7))<1||Number(value.slice(5,7))>12))return undefined
 if(mode==='day'){
  const instant=new Date(value+'T00:00:00Z')
  if(!Number.isFinite(instant.getTime())||instant.toISOString().slice(0,10)!==value)return undefined
 }
 return value
}

export function zonedCalendar(value:string,zone:string){
 try{
  const instant=new Date(value)
  if(!Number.isFinite(instant.getTime()))return null
  const parts=new Intl.DateTimeFormat('en-GB',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(instant)
  const field=(name:Intl.DateTimeFormatPartTypes)=>parts.find(part=>part.type===name)?.value||''
  const year=field('year').padStart(4,'0'),month=field('month'),day=field('day'),hour=field('hour'),minute=field('minute')
  if(![year,month,day,hour,minute].every(value=>/^\d+$/.test(value)))return null
  return {year,month,day,hour,minute,dateKey:year+'-'+month+'-'+day}
 }catch{return null}
}

export function todayCalendarDate(zone:string,instant=new Date().toISOString()){
 return zonedCalendar(instant,zone)?.dateKey||''
}

export function reelDateTime(value:string,zone:string){
 const parts=zonedCalendar(value,zone)
 return parts?`${parts.day}/${parts.month}/${parts.year} ${parts.hour}:${parts.minute}`:'Chưa xác định ngày giờ'
}

export function localDateTimePreview(value:string){
 const match=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value)
 return match?`${match[3]}/${match[2]}/${match[1]} ${match[4]}:${match[5]}`:'Chưa chọn ngày giờ'
}

export function calendarHeading(value:string,zone:string,group:CalendarGroup){
 const parts=zonedCalendar(value,zone)
 if(!parts)return {key:'unknown',label:'Chưa xác định ngày'}
 if(group==='year')return {key:parts.year,label:parts.year}
 if(group==='month')return {key:parts.year+'-'+parts.month,label:parts.month+'/'+parts.year}
 return {key:parts.dateKey,label:parts.day+'/'+parts.month+'/'+parts.year}
}
