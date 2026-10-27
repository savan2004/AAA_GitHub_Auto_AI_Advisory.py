"use strict";
// Zero-cost MSK MVP: data ONLY from user's CSV or clearly-labelled synthetic demo.
(function(){
 const el=id=>document.getElementById(id);
 const st={data:[],calc:null,kind:"none",file:"",tab:"overview"};
 const fm=(x,n=2)=>Number.isFinite(x)?x.toLocaleString("en-IN",{maximumFractionDigits:n,minimumFractionDigits:n}):"Unavailable";
 const sym=()=> (el("ticker").value||"UNKNOWN").toUpperCase().replace(/[^A-Z0-9&._-]/g,"").slice(0,22);
 const api=()=>typeof window.MSK_WEB_API==="string"?window.MSK_WEB_API.replace(/\/+$/,""):"";
 function csvParse(text){
   const lines=text.replace(/^\uFEFF/,"").replace(/\r/g,"").split("\n").filter(s=>s.trim());
   if(lines.length<51)throw Error("At least 50 complete daily candles required.");
   if(lines.length>5500)throw Error("CSV too large (maximum 5500 rows).");
   const parse=line=>{let out=[],part="",quote=false;for(let i=0;i<line.length;i++){let c=line[i];if(c==='"'){if(quote&&line[i+1]==='"'){part+='"';i++;}else quote=!quote;}else if(c===','&&!quote){out.push(part.trim());part="";}else part+=c;}out.push(part.trim());return out;};
   const head=parse(lines[0]).map(v=>v.toLowerCase().trim());
   const required=["timestamp","open","high","low","close","volume"];
   if(!required.every(k=>head.includes(k)))throw Error("CSV must include timestamp,open,high,low,close,volume.");
   const idx=Object.fromEntries(required.map(x=>[x,head.indexOf(x)]));
   const data=[];
   for(const line of lines.slice(1)){
     const parts=parse(line),d=new Date(parts[idx.timestamp]);
     if(isNaN(d.getTime()))throw Error("Invalid timestamp.");
     const x={date:d.toISOString().slice(0,10)};
     for(const key of required.slice(1)){if(!parts[idx[key]]||!Number.isFinite(Number(parts[idx[key]])))throw Error("Invalid "+key);x[key]=Number(parts[idx[key]]);}
     if(x.open<=0||x.high<=0||x.low<=0||x.close<=0||x.volume<0||x.high<Math.max(x.open,x.close)||x.low>Math.min(x.open,x.close))
       throw Error("Invalid candle: high/low conflict or negative volume.");
     data.push(x);
   }
   data.sort((a,b)=>a.date.localeCompare(b.date));
   for(let i=1;i<data.length;i++)if(data[i].date===data[i-1].date)throw Error("Duplicate daily date.");
   if(data.length<50)throw Error("Need at least 50 valid daily bars.");
   return data.slice(-500);
 }
 function demo(){
   let a=[],price=1200,seed=41077,day=0;
   function random(){seed=seed*48271%2147483647;return seed/2147483647;}
   while(a.length<156){
     let d=new Date(Date.UTC(2026,2,2+day++));
     if(d.getUTCDay()===0||d.getUTCDay()===6)continue;
     const drift=a.length<80?.8:a.length<113?-.95:.26;
     let open=price+(random()-.5)*6,close=Math.max(100,open+(random()-.5)*14+drift);
     let high=Math.max(open,close)+random()*9+1,low=Math.min(open,close)-random()*9-1;
     let volume=Math.round(780000*(.55+random()*1.1)*(a.length===155?2.7:1));
     a.push({date:d.toISOString().slice(0,10),open,high,low,close,volume});price=close;
   }return a;
 }
 function ema(series,period){const alpha=2/(period+1),out=[];let p=series[0];for(const v of series){p=out.length?v*alpha+p*(1-alpha):v;out.push(p);}return out;}
 const mean=a=>a.reduce((s,x)=>s+x,0)/a.length;
 function adx14(data,p=14){
   let tr=[],plus=[],minus=[];
   for(let i=1;i<data.length;i++){const a=data[i],b=data[i-1],up=a.high-b.high,down=b.low-a.low;
     tr.push(Math.max(a.high-a.low,Math.abs(a.high-b.close),Math.abs(a.low-b.close)));
     plus.push(up>down&&up>0?up:0);minus.push(down>up&&down>0?down:0);
   }
   if(tr.length<2*p)return null;
   let a=tr.slice(0,p).reduce((s,v)=>s+v,0),b=plus.slice(0,p).reduce((s,v)=>s+v,0),c=minus.slice(0,p).reduce((s,v)=>s+v,0),dx=[];
   for(let i=p;i<tr.length;i++){a=a-a/p+tr[i];b=b-b/p+plus[i];c=c-c/p+minus[i];
     let u=a?b/a*100:0,v=a?c/a*100:0;dx.push(u+v?Math.abs(u-v)/(u+v)*100:0);}
   if(dx.length<p)return null;
   let z=mean(dx.slice(0,p));for(let i=p;i<dx.length;i++)z=(z*(p-1)+dx[i])/p;
   return z;
 }
 function regression(values){const ybar=mean(values),xbar=(values.length-1)/2;
   let xx=0,xy=0;for(let i=0;i<values.length;i++){xx+=(i-xbar)**2;xy+=(i-xbar)*(values[i]-ybar);}
   const slope=xx?xy/xx:0,intercept=ybar-slope*xbar;
   return {slope,intercept,std:Math.sqrt(mean(values.map((v,i)=>(v-(intercept+slope*i))**2)))};
 }
 function compute(data){
   const closes=data.map(x=>x.close),volumes=data.map(x=>x.volume);
   const e20=ema(closes,20),e50=ema(closes,50),a=adx14(data),last=data.at(-1);
   const avg=mean(volumes.slice(-21,-1)),ratio=avg?last.volume/avg:null;
   const prevH=Math.max(...data.slice(-21,-11).map(x=>x.high)),newH=Math.max(...data.slice(-11).map(x=>x.high));
   const prevL=Math.min(...data.slice(-21,-11).map(x=>x.low)),newL=Math.min(...data.slice(-11).map(x=>x.low));
   let trend="Sideways";if(e20.at(-1)>e50.at(-1)&&newH>prevH)trend="Uptrend";
   if(e20.at(-1)<e50.at(-1)&&newL<prevL)trend="Downtrend";
   const adxLabel=a===null?"Unavailable":a<20?"Weak":a<=25?"Trend building":a<=35?"Strong":"Very strong";
   const volLabel=ratio===null?"Unavailable":ratio<1.5?"Normal":ratio<2?"Above normal":ratio<3?"High":"Explosive";
   let pattern="No clean pattern",signal="WAIT",quality="Weak";
   // Strict final-candle channel breakout, volume + ADX confirmation. No forced label.
   const oldHigh=Math.max(...data.slice(-21,-1).map(x=>x.high)),oldLow=Math.min(...data.slice(-21,-1).map(x=>x.low));
   const bullish=last.close>oldHigh,bearish=last.close<oldLow;
   if(ratio!==null&&ratio>=2&&a!==null&&a>25&&(bullish||bearish)){
     pattern="Channel Breakout";
     const crossUp=e20.at(-1)>e50.at(-1)&&e20.at(-2)<=e50.at(-2);
     const crossDown=e20.at(-1)<e50.at(-1)&&e20.at(-2)>=e50.at(-2);
     if(trend==="Uptrend"&&bullish&&crossUp){signal="BUY";quality="High";}
     if(trend==="Downtrend"&&bearish&&crossDown){signal="SELL";quality="High";}
   }
   return {date:last.date,last:last.close,e20,e50,adx:a,adxLabel,ratio,volLabel,trend,pattern,signal,quality,reg:regression(closes.slice(-42))};
 }
 const text=(id,v)=>el(id).textContent=v;
 function dataLoaded(rows,kind,file){
   st.data=rows;st.kind=kind;st.file=file;st.calc=compute(rows);
   const c=st.calc;
   if(kind==="tej_eod"){
     document.body.classList.add("market-eod");
     // A strategy cannot be called trade-ready without measured out-of-sample
     // accuracy; compute indicators but never publish actionable BUY/SELL.
     c.signal="RESEARCH ONLY";c.quality="Not backtested";
   }else document.body.classList.remove("market-eod");
   text("lastPrice","₹"+fm(c.last));text("priceMeta",c.date+" • "+(kind==="synthetic"?"SIMULATED":kind==="tej_eod"?"NSE EOD (TejHQ) • not live":"user CSV (unverified)"));
   text("trend",c.trend);text("adx",c.adx===null?"—":fm(c.adx,1));
   text("adxLabel",c.adxLabel);text("vol",c.ratio===null?"—":fm(c.ratio,2)+"x");text("volLabel",c.volLabel);
   text("chartTitle",sym()+" • Historical daily candlesticks");
   text("analysisTitle",sym()+" Quant Readout");text("signal",c.signal);
   const panel=el("analysisLines");panel.replaceChildren();
   for(const pair of [["Trend",c.trend],["Pattern",c.pattern],["ADX",c.adx===null?"Unavailable":fm(c.adx,1)+" "+c.adxLabel],
     ["Volume",c.ratio===null?"Unavailable":fm(c.ratio,2)+"x "+c.volLabel],["Signal",c.signal],["Quality",c.quality]]){
     let r=document.createElement("div"),l=document.createElement("span"),v=document.createElement("b");
     l.textContent=pair[0];v.textContent=pair[1];r.append(l,v);panel.append(r);
   }
   text("dataLabel",(kind==="synthetic"?"SYNTHETIC DEMO — NOT REAL NSE":
        kind==="tej_eod"?"NSE END-OF-DAY • NOT INTRADAY/LIVE • TejHQ-derived bhavcopy (raw, unadjusted)":
        "USER CSV — NOT INDEPENDENTLY VERIFIED")+" • "+file+" • "+rows.length+" daily bars • Last trading session "+c.date);
   el("dataLabel").classList.add("hasData");el("chartEmpty").style.display="none";
   text("analysisNote",kind==="synthetic"?"Simulated test data. NOT a real market recommendation.":
     kind==="tej_eod"?"Daily exchange-bhavcopy-derived data from TejHQ; unadjusted for corporate actions. Experimental indicators, NOT verified trade signals.":
     "User-supplied source has not been exchange-verified; no guaranteed signal.");
   draw();renderTab();
 }
 function draw(){
   if(!st.data.length)return;
   const canvas=el("chartCanvas"),width=Math.max(300,canvas.clientWidth),height=canvas.clientHeight||390,dpr=Math.min(2,window.devicePixelRatio||1);
   canvas.width=width*dpr;canvas.height=height*dpr;const ctx=canvas.getContext("2d");ctx.setTransform(dpr,0,0,dpr,0,0);
   ctx.fillStyle="#0b1424";ctx.fillRect(0,0,width,height);
   const bars=st.data.slice(-80),n=bars.length,offset=st.data.length-n,left=13,right=75,top=22,bottom=height-95,W=width-left-right,w=W/n;
   const hi=Math.max(...bars.map(x=>x.high)),lo=Math.min(...bars.map(x=>x.low)),span=Math.max(hi-lo,2),y0=lo-span*.10,y1=hi+span*.14;
   const py=v=>bottom-(v-y0)/(y1-y0)*(bottom-top);
   ctx.font="10px sans-serif";ctx.textAlign="right";
   for(let i=0;i<=5;i++){const value=y0+(y1-y0)*i/5,y=py(value);ctx.strokeStyle="#203147";ctx.lineWidth=.8;
      ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(width-right+4,y);ctx.stroke();ctx.fillStyle="#93a9c5";ctx.fillText("₹"+Math.round(value).toLocaleString("en-IN"),width-3,y+3);}
   bars.forEach((b,i)=>{const x=left+(i+.5)*w,col=b.close>=b.open?"#32dec6":"#fc6f85";ctx.strokeStyle=col;ctx.fillStyle=col;ctx.lineWidth=1.2;
     ctx.beginPath();ctx.moveTo(x,py(b.high));ctx.lineTo(x,py(b.low));ctx.stroke();
     const start=Math.min(py(b.open),py(b.close)),body=Math.max(1.4,Math.abs(py(b.open)-py(b.close)));
     ctx.fillRect(x-w*.32,start,Math.max(1.5,w*.64),body);});
   function emaLine(series,color){ctx.strokeStyle=color;ctx.lineWidth=1.8;ctx.beginPath();
     for(let i=0;i<n;i++){const x=left+(i+.5)*w,y=py(series[offset+i]);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);}ctx.stroke();}
   emaLine(st.calc.e20,"#ffcb69");emaLine(st.calc.e50,"#85a6ff");
   const reg=st.calc.reg,L=Math.min(42,n),idx=n-L,half=Math.max(reg.std*1.6,span*.045);
   ctx.strokeStyle="rgba(93,209,228,.56)";ctx.lineWidth=1;ctx.setLineDash([4,4]);
   for(const dir of [-1,1]){ctx.beginPath();for(let i=0;i<L;i++){const x=left+(idx+i+.5)*w,y=py(reg.intercept+reg.slope*i+dir*half);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);}ctx.stroke();}ctx.setLineDash([]);
   const maxVol=Math.max(...bars.map(x=>x.volume),1),by=height-24;
   bars.forEach((b,i)=>{ctx.fillStyle=b.close>=b.open?"rgba(50,222,198,.48)":"rgba(252,111,133,.48)";
     const h=b.volume/maxVol*65;ctx.fillRect(left+(i+.2)*w,by-h,Math.max(1,w*.6),h);});
   ctx.textAlign="center";ctx.fillStyle="#879eb8";const jump=Math.max(12,Math.ceil(n/6));
   for(let i=0;i<n;i+=jump)ctx.fillText(bars[i].date.slice(5),left+(i+.5)*w,height-4);
   canvas.dataset.n=n;canvas.dataset.w=w;canvas.dataset.left=left;
 }
 function paragraph(parent,content,cls){let p=document.createElement("p");p.textContent=content;p.className=cls||"";parent.append(p);}
 function renderTab(){
   const panel=el("detailBody"),tab=st.tab,c=st.calc;
   const titles={overview:"Research Overview",technical:"Technical Analysis",fundamental:"Fundamental Research",chart:"Trading Chart",
     screener:"Stock Screener",breadth:"Market Breadth",pdf:"Research Report",ai:"Direct AI Analyst"};
   text("detailTitle",titles[tab]||"Overview");text("detailLabel",tab.toUpperCase()+" • SOURCE AWARE");panel.replaceChildren();
   el("analysisRegion").hidden=tab==="ai";
   if(window.MSKWeb&&window.MSKWeb.features&&window.MSKWeb.features.renderPanel(tab,panel))return;
   if(tab==="ai"){paragraph(panel,"AI chat is below. It requires a secure server-side provider connection; this preview never exposes or guesses API keys.");return;}
   if(!c){paragraph(panel,"No stock data loaded. Import your own OHLCV CSV or choose the SYNTHETIC demo to test the chart.","warning");return;}
   if(st.kind==="synthetic")paragraph(panel,"SIMULATED EXAMPLE. These numbers are not actual Indian stocks.","warning");
   else if(st.kind==="tej_eod")paragraph(panel,"NSE end-of-day bhavcopy-derived OHLCV served by TejHQ. Last dated session: "+c.date+". Raw/unadjusted history, NOT live quote. Source and rights for public or commercial redistribution require confirmation.","warning");
   else paragraph(panel,"Uploaded CSV: "+st.file+". Claimed session date: "+c.date+". Original source not independently verified.","warning");
   if(["overview","technical","chart"].includes(tab)){
     paragraph(panel,"Trend "+c.trend+". EMA20 ₹"+fm(c.e20.at(-1))+"; EMA50 ₹"+fm(c.e50.at(-1))+". ADX "+fm(c.adx,1)+" ("+c.adxLabel+"). Volume "+(c.ratio===null?"Unavailable":fm(c.ratio,2)+"x "+c.volLabel)+".");
     paragraph(panel,"Pattern observation: "+c.pattern+". Model status: "+c.signal+"; validation: "+c.quality+". Not backtested; no executable BUY/SELL signal.");
     paragraph(panel,"Volume ratio compares current volume against the PREVIOUS twenty candles only. Candles/wicks are from supplied OHLC data.");
   }else if(tab==="fundamental"){
     paragraph(panel,"Unavailable: audited P/E, ROE, revenue growth, balance sheet, earnings date and peers. OHLCV history does not contain company accounts. Do not invent.","warning");
   }else if(tab==="screener"){
     paragraph(panel,"One uploaded instrument is not a verified stock universe. Multi-stock screening requires aligned licensed data and constituent membership.");
   }else if(tab==="breadth"){
     paragraph(panel,"Market breadth requires observations for hundreds of verified constituents; one CSV cannot truthfully generate an A/D ratio or Nifty breadth.","warning");
   }else if(tab==="pdf"){
     paragraph(panel,"Use Print / Save as PDF for this local research view. Disclaimer, data source and chart remain visible.");
     const button=document.createElement("button");button.textContent="Print / Save PDF";button.className="btn";button.onclick=()=>window.print();panel.append(button);
   }
 }
 function bubble(role,msg){let x=document.createElement("div");x.className="bubble "+role;x.textContent=msg;el("chatHistory").append(x);el("chatHistory").scrollTop=el("chatHistory").scrollHeight;}
 async function ask(question){
   const base=api();if(!base){
     if(window.MSKWeb&&window.MSKWeb.features){bubble("assistant",window.MSKWeb.features.offlineAnswer(question));}
     else bubble("assistant","Generative AI not connected. Add a secure server endpoint; model keys must not be exposed in the browser.");
     return;
   }
   if(st.kind==="synthetic"){
     bubble("assistant","SYNTHETIC DEMO • No market data sent to an external AI provider.\n"+
       (window.MSKWeb?.features?.offlineAnswer(question)||"Local evidence not available."));
     return;
   }
   const ctrl=new AbortController(),timer=setTimeout(()=>ctrl.abort(),14000);
   try{
     const payload={question:question.slice(0,550),symbol:sym(),
       context:st.calc?{source:st.kind,asof:st.calc.date,trend:st.calc.trend,adx:st.calc.adx,volume_multiple:st.calc.ratio,pattern:st.calc.pattern,signal:st.calc.signal}:null};
     const res=await fetch(base+"/api/ai/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal:ctrl.signal,credentials:"omit"});
     if(res.status===503||res.status===403){
       bubble("assistant","Neon backend reached, but generative AI is not yet authorized or configured. "+
         "Here is a clearly labelled local rule-based answer:\n\n"+
         (window.MSKWeb?.features?.offlineAnswer(question)||"No local research context."));
       return;
     }
     if(!res.ok)throw Error("HTTP "+res.status);
     const value=await res.json();
     bubble("assistant",typeof value.answer==="string"&&value.answer?value.answer:
       (window.MSKWeb?.features?.offlineAnswer(question)||"No verified AI response."));
   }catch(err){
     bubble("assistant","Backend connection unavailable ("+err.message+"). "+
       "Local evidence answer:\n"+(window.MSKWeb?.features?.offlineAnswer(question)||"Not available."));
   }finally{clearTimeout(timer);}
 }
 function draft(){
   const c=st.calc;if(!c){bubble("assistant","No data loaded. Import a verified-source CSV to create a transparent evidence summary.");return;}
   bubble("assistant","DETERMINISTIC SUMMARY — NOT GENERATIVE AI\n"+sym()+" | "+(st.kind==="synthetic"?"SYNTHETIC DEMO":st.kind==="tej_eod"?"NSE EOD / TejHQ / NOT LIVE":"USER CSV / UNVERIFIED")+
     "\nSession: "+c.date+"\nTrend: "+c.trend+"\nEMA20: ₹"+fm(c.e20.at(-1))+" | EMA50: ₹"+fm(c.e50.at(-1))+
     "\nADX: "+fm(c.adx,1)+" "+c.adxLabel+"\nVolume: "+(c.ratio===null?"unavailable":fm(c.ratio,2)+"x "+c.volLabel)+
     "\nPattern: "+c.pattern+" | Signal: "+c.signal+"\nRisk: no source audit, no historical signal backtest and no issuer filings imported.\nNext checks: exchange session/adjustments, financial statements, risk budget.");
 }
 el("loadSample").addEventListener("click",()=>dataLoaded(demo(),"synthetic","Generated test series"));
 el("csvFile").addEventListener("change",async e=>{let f=e.target.files&&e.target.files[0];if(!f)return;
   if(f.size>2e6){alert("CSV file limit 2MB.");return;}
   try{dataLoaded(csvParse(await f.text()),"uploaded",f.name);}catch(err){alert("CSV rejected: "+err.message);}
 });
 document.querySelectorAll(".nav").forEach(btn=>btn.addEventListener("click",()=>{
   document.querySelectorAll(".nav").forEach(b=>b.classList.toggle("active",b===btn));
   st.tab=btn.dataset.tab;renderTab();if(st.data.length)requestAnimationFrame(draw);
 }));
 el("explain").addEventListener("click",draft);
 el("chatForm").addEventListener("submit",e=>{e.preventDefault();const inp=el("chatInput"),q=inp.value.trim();
   if(q){bubble("user",q);inp.value="";ask(q);}
 });
 el("chartCanvas").addEventListener("mousemove",e=>{
   const can=e.target,n=Number(can.dataset.n),w=Number(can.dataset.w),left=Number(can.dataset.left);
   if(!n||!w)return;
   const i=Math.floor((e.clientX-can.getBoundingClientRect().left-left)/w);
   if(i<0||i>=n)return;
   const d=st.data[st.data.length-n+i];
   text("hoverBar",d.date+"  O ₹"+fm(d.open)+"  H ₹"+fm(d.high)+"  L ₹"+fm(d.low)+"  C ₹"+fm(d.close)+"  VOL "+Math.round(d.volume).toLocaleString("en-IN"));
 });
 window.addEventListener("resize",()=>{if(st.data.length)draw();});
 window.MSKWeb={state:st,dataLoaded,csvParse,demo,compute,draw,renderTab,paragraph,bubble,symbol:sym,format:fm};
 text("backendIndicator",api()?"Checking research API…":"Local research • AI provider offline");
 if(api() && typeof fetch === "function") {
   fetch(api()+"/api/health",{credentials:"omit",
     signal:typeof AbortSignal!=="undefined"&&typeof AbortSignal.timeout==="function"?
       AbortSignal.timeout(8000):undefined})
     .then(async response=>{
       if(!response.ok)throw Error("health "+response.status);
       const body=await response.json();
       text("backendIndicator",body.ok?
         ("Neon API online • AI "+(body.generative_ai==="disabled"?"not enabled":"login required")):
         "Research API did not pass health check");
     })
     .catch(()=>text("backendIndicator","Research API offline • CSV research available"));
 }
 renderTab();
})();