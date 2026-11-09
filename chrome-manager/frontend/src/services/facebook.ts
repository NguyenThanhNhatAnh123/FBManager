// Use Intl's IANA database for calendar dates and DST-aware midnight boundaries.
export function facebookDay(timezone:string, at=new Date()) {
  const formatter=new Intl.DateTimeFormat('en-CA',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'})
  function parts(date:Date){const p=Object.fromEntries(formatter.formatToParts(date).map(p=>[p.type,p.value]));return [Number(p.year),Number(p.month),Number(p.day),Number(p.hour),Number(p.minute),Number(p.second)]}
  const [year,month,day]=parts(at)
  function midnight(y:number,m:number,d:number){
    const target=Date.UTC(y,m-1,d)
    let value=target
    for(let i=0;i<4;i++){
      const [py,pm,pd,ph,pmin,ps]=parts(new Date(value))
      const delta=target-Date.UTC(py,pm-1,pd,ph,pmin,ps)
      value+=delta
      if(!delta)break
    }
    return new Date(value).toISOString()
  }
  const next=new Date(Date.UTC(year,month-1,day+1))
  return {timezone,check_date:`${year}-${String(month).padStart(2,'0')}-${String(day).padStart(2,'0')}`,day_start:midnight(year,month,day),day_end:midnight(next.getUTCFullYear(),next.getUTCMonth()+1,next.getUTCDate())}
}
export const facebookActive=(profile:{facebook?:{status:string}|null})=>['queued','checking'].includes(profile.facebook?.status||'')
export const accountLabels:Record<string,string>={unchecked:'Chưa kiểm tra',queued:'Chờ kiểm tra',checking:'Đang kiểm tra',complete:'Đã cập nhật',partial:'Chưa đủ dữ liệu',login_required:'Cần đăng nhập / xác minh',error:'Kiểm tra lỗi',cancelled:'Đã dừng'}
