import { useEffect, useRef, type ReactNode } from 'react'
import { X, Layers, LoaderCircle } from 'lucide-react'
export function Badge({status}:{status:string}) {
  const color=['Running','Working','Completed'].includes(status)?'green':['Failed','Error','Timeout'].includes(status)?'red':['Queued','Pending','Starting','Checking'].includes(status)?'amber':'gray'
  return <span className={'badge '+color}><i/>{status}</span>
}
export function Modal({title,subtitle,children,close,wide=false}:{title:string;subtitle?:string;children:ReactNode;close:()=>void;wide?:boolean}){
  const ref=useRef<HTMLDialogElement>(null)
  const backdropPress=useRef(false)
  useEffect(()=>{ref.current?.showModal()},[])
  function outside(x:number,y:number){
    const bounds=ref.current?.getBoundingClientRect()
    return !!bounds&&(x<bounds.left||x>bounds.right||y<bounds.top||y>bounds.bottom)
  }
  return <dialog ref={ref} className={'modal '+(wide?'wide':'')} onCancel={close}
    onPointerDown={e=>{backdropPress.current=e.target===e.currentTarget&&e.button===0&&outside(e.clientX,e.clientY)}}
    onPointerCancel={()=>{backdropPress.current=false}}
    onClick={e=>{const beganOutside=backdropPress.current;backdropPress.current=false;if(beganOutside&&e.target===e.currentTarget&&outside(e.clientX,e.clientY))close()}}>
    <div className="modal-head"><div><h2>{title}</h2>{subtitle&&<p>{subtitle}</p>}</div><button className="icon-button" aria-label="Close dialog" onClick={close}><X size={20}/></button></div>{children}
  </dialog>
}
export function Empty({title,detail,children}:{title:string;detail:string;children?:ReactNode}){
  return <div className="empty"><div className="empty-icon"><Layers size={26}/></div><h3>{title}</h3><p>{detail}</p>{children}</div>
}
export function Submit({busy,label='Save',disabled=false}:{busy:boolean;label?:string;disabled?:boolean}){return <button className="button primary" type="submit" disabled={busy||disabled}>{busy&&<LoaderCircle size={15} className="spin"/>}{busy?'Working…':label}</button>}
export function Progress({value}:{value:number}){return <div className="progress-row"><div className="progress"><i style={{width:value+'%'}}/></div><span>{value}%</span></div>}
export function time(value:string|null){return value?new Date(value).toLocaleString(): '—'}
