(function(){
 // Mixes the embedded narration (NARR.b64) into the scene's SOUND kit. The wrap is attached when the scene says it is
 // ready (window.__ready = true): scenes that define SOUND in a <script type="module"> only have it by then.
 // If the wrap fails, __ready is NOT set, so Shadow's render stops with an error instead of making a video with no voice.
 function pcm(){ const s=atob(NARR.b64), n=s.length; const u=new Uint8Array(n); for(let i=0;i<n;i++)u[i]=s.charCodeAt(i);
  const dv=new DataView(u.buffer); const rate=dv.getUint32(24,true); const ch=dv.getUint16(22,true); let off=12; while(off<n-8){ const id=String.fromCharCode(u[off],u[off+1],u[off+2],u[off+3]), sz=dv.getUint32(off+4,true); if(id==='data'){off+=8;break;} off+=8+sz; }
  const m=Math.floor((n-off)/2/ch), f=new Float32Array(m); for(let i=0;i<m;i++) f[i]=dv.getInt16(off+i*2*ch,true)/32768; return {f,rate}; }
 function wrap(){
  if(!window.SOUND||typeof window.SOUND.renderOffline!=='function') throw new Error('the scene has no SOUND.renderOffline to mix the narration into');
  if(window.SOUND.__narrWrapped) return;
  const SOUND=window.SOUND, origOff=SOUND.renderOffline;
  SOUND.renderOffline=async function(fn,duration,sr,opts){ sr=sr||48000;
   const r=await origOff.call(SOUND,fn,duration,sr,Object.assign({},opts,{voiceover:true}));   // voiceover mode: beds/music dropped, minor sounds quieter
   const v=pcm(), ratio=v.rate/sr; const inter=r.inter; let peak=0;
   for(let i=0;i<r.frames;i++){ const p=i*ratio, j=Math.floor(p); if(j+1>=v.f.length) break; const x=(v.f[j]*(1-(p-j))+v.f[j+1]*(p-j))*NARR.gain; inter[2*i]+=x; inter[2*i+1]+=x; }
   for(let i=0;i<inter.length;i++) peak=Math.max(peak,Math.abs(inter[i]));
   return {inter:inter,peak:peak,frames:r.frames,sr:r.sr}; };
  const origLive=SOUND.live;
  if(typeof origLive==='function') SOUND.live=function(fn,fromT,opts){ const stop=origLive.call(SOUND,fn,fromT,Object.assign({},opts,{voiceover:true}));
   const a=new Audio('data:audio/wav;base64,'+NARR.b64); a.currentTime=Math.max(0,fromT||0); a.play().catch(()=>{});
   return ()=>{ try{a.pause();}catch(e){} stop(); }; };
  SOUND.__narrWrapped=true; window.__narrWrapped=true;
 }
 function fail(e){
  const msg='narration: '+((e&&e.message)||e); window.__narrError=msg; console.error(msg);
  setTimeout(()=>{ throw new Error(msg); });   // a page error, so Shadow's "scene did not load" message says why
 }
 let ready=false;
 function arm(v){
  if(v===true&&!window.__narrWrapped){ try{ wrap(); ready=true; }catch(e){ ready=false; fail(e); } }
  else ready=v;
 }
 const already=window.__ready;          // a scene that was ready before this block ran (classic scripts only)
 Object.defineProperty(window,'__ready',{configurable:true,get(){return ready;},set:arm});
 if(already!==undefined) arm(already);
})();
