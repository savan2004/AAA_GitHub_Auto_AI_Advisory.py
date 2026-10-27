"use strict";
/**
 * MSK Data Import Center — privacy-first, client-side only.
 * One discoverable import entry for price OHLCV, company filings and holdings.
 * No file bytes leave the browser. No XLSX claim (CSV only, save Excel as CSV).
 */
(() => {
 const $=id=>document.getElementById(id);
 const F=window.MSKWeb?.features;
 if(!F)return;
 const openBtn=$("openImport"),modal=$("importModal");
 if(!openBtn||!modal)throw Error("Import Center markup missing");
 const panel=$("importRows"),message=$("importFeedback"),input=$("importFile");
 const tabs=[...modal.querySelectorAll("[data-import-kind]")];
 let mode="price",current=null;
 const templates={
   price:"symbol,timestamp,open,high,low,close,volume\nBEL,2026-10-26,,,,,\n",
   fundamental:"symbol,asof,source,pe,roe_percent,revenue_growth_percent,market_cap_inr,debt_equity_ratio\nBEL,2026-09-30,Exchange filing URL,,,,,\n",
   portfolio:"symbol,quantity,buy_price\nBEL,10,100\n"
 };
 const required={
   price:["timestamp","open","high","low","close","volume"],
   fundamental:["symbol","asof","source","pe","roe_percent","revenue_growth_percent","market_cap_inr","debt_equity_ratio"],
   portfolio:["symbol","quantity","buy_price"]
 };
 const rowsOf=t=>{
   const lines=String(t).replace(/^\uFEFF/,"").replace(/\r\n?/g,"\n").split("\n").filter(x=>x.trim());
   const items=[];
   for(const line of lines){
     let value="",quoted=false,row=[];
     for(let j=0;j<line.length;j++){
       const ch=line[j];
       if(ch==='"'&&quoted&&line[j+1]==='"'){value+='"';j++;}
       else if(ch==='"')quoted=!quoted;
       else if(ch===","&&!quoted){row.push(value.trim());value="";}
       else value+=ch;
     }row.push(value.trim());items.push(row);
   }return items;
 };
 const safe=s=>String(s||"").slice(0,200);
 function setMessage(m,bad=false){message.textContent=m;message.dataset.state=bad?"error":"success";message.setAttribute("role",bad?"alert":"status");}
 function select(modeNext){
   mode=modeNext;current=null;
   input.value="";panel.replaceChildren();
   for(const t of tabs){const active=t.dataset.importKind===mode;t.classList.toggle("active",active);t.setAttribute("aria-selected",String(active));}
   $("importKindTitle").textContent={
     price:"Historical price candles",
     fundamental:"Financial statement metrics",
     portfolio:"Personal holdings — local only"
   }[mode];
   $("importHelp").textContent={
     price:"Use CSV (not XLSX): one row per trading session. 50+ valid daily candles per symbol. A SYMBOL column supports multiple stocks.",
     fundamental:"Use source-dated company financial metrics. Figures are USER-SUPPLIED, never independently audited; OHLC candles do not include P/E or ROE.",
     portfolio:"Upload symbol, quantity, buy price. Holdings stay inside this browser session; nothing is stored on Neon or Telegram. Only imported stock close prices can be used for indicative value."
   }[mode];
   setMessage("Select a .csv file. The first rows will be shown here before importing.");
   $("confirmImport").disabled=true;
 }
 function close(){modal.hidden=true;document.body.classList.remove("import-open");openBtn.focus();}
 function open(kind){
   modal.hidden=false;document.body.classList.add("import-open");
   select(["price","fundamental","portfolio"].includes(kind)?kind:"price");
   $("closeImport").focus();
 }
 function preview(text){
   const rows=rowsOf(text);
   if(rows.length<2)throw Error("File must contain a header and at least one data row.");
   const columns=rows[0].map(x=>x.toLowerCase().replace(/\s/g,""));
   const missing=required[mode].filter(x=>!columns.includes(x));
   if(missing.length)throw Error("Missing columns: "+missing.join(", ")+". Download the correct template.");
   if(rows.some((row,i)=>i>0&&row.length!==columns.length))
     throw Error("Some CSV rows have a different number of fields from the header.");
   panel.replaceChildren();
   const table=document.createElement("table"),thead=document.createElement("thead"),tr=document.createElement("tr");
   for(const h of columns){const th=document.createElement("th");th.textContent=h;tr.append(th);}thead.append(tr);table.append(thead);
   const body=document.createElement("tbody");
   for(const values of rows.slice(1,7)){
     const item=document.createElement("tr");
     for(const v of values){const td=document.createElement("td");td.textContent=safe(v);item.append(td);}body.append(item);
   }table.append(body);panel.append(table);
   $("previewCount").textContent=(rows.length-1)+" data row(s) • first "+Math.min(6,rows.length-1)+" previewed";
   return rows.length-1;
 }
 async function prepare(file){
   current=null;$("confirmImport").disabled=true;panel.replaceChildren();
   if(!file)return setMessage("Select a CSV first.",true);
   if(!/\.csv$/i.test(file.name))return setMessage("Only .csv files are supported. In Excel use File → Save As → CSV UTF-8.",true);
   if(file.size>2100000)return setMessage("File is too large (2 MB limit).",true);
   setMessage("Reading "+safe(file.name)+"…");
   try{
     const data=await file.text();
     const n=preview(data);
     current={text:data,name:file.name};
     $("confirmImport").disabled=false;
     setMessage("Preview ready: "+n+" row(s). Press Import to validate all rows and apply.");
   }catch(e){setMessage("CSV preview rejected: "+e.message,true);}
 }
 function holdings(text){
   const rows=rowsOf(text),header=rows[0].map(x=>x.toLowerCase()),idx=s=>header.indexOf(s);
   if(rows.length>301)throw Error("Maximum 300 holdings.");
   const parsed=[];
   for(let i=1;i<rows.length;i++){
     const row=rows[i],symbol=String(row[idx("symbol")]||"").toUpperCase().trim();
     const qty=Number(row[idx("quantity")]),buy=Number(row[idx("buy_price")]);
     if(!/^[A-Z][A-Z0-9&._-]{0,20}$/.test(symbol)||!Number.isFinite(qty)||qty<=0||
        !Number.isFinite(buy)||buy<=0)throw Error("Invalid holdings on CSV row "+(i+1));
     parsed.push({symbol,qty,buy});
   }
   // No server persistence, no made-up market value for missing symbols.
   const available=F.library;
   let cost=0,value=0,matched=0;
   const detail=[];
   for(const h of parsed){
     const last=available.get(h.symbol)?.bars?.at(-1);
     const mode=available.get(h.symbol)?.source||"none";
     const known=last&&mode==="tej_eod";
     cost+=h.qty*h.buy;
     if(known){matched++;value+=h.qty*last.close;}
     detail.push({symbol:h.symbol,quantity:h.qty,buy_price:h.buy,source:mode,
       historical_close:known?last.close:null,asof:known?last.date:null,
       estimated_value:known?h.qty*last.close:null});
   }
   window.MSKPortfolio={rows:detail,quantity:parsed.length,invested:cost,
     priced:matched,priced_value:value,createdAt:Date.now()};
   document.dispatchEvent(new CustomEvent("msk:portfolio-import",{detail:{count:parsed.length}}));
   return {count:parsed.length,matched};
 }
 function confirm(){
   if(!current)return setMessage("No preview has been validated.",true);
   $("confirmImport").disabled=true;
   setMessage("Validating and importing "+safe(current.name)+"…");
   try{
     if(mode==="price")F.importBars(current.text,current.name);
     else if(mode==="fundamental")F.importFundamentals(current.text);
     else {
       const x=holdings(current.text);
       $("importReport").textContent="Local holdings imported: "+x.count+
         ". Historic price matches: "+x.matched+". No unaudited profit figure calculated.";
     }
     setMessage("Imported successfully: "+safe(current.name)+". Source data remains in this browser session.");
     const target=mode==="price"?"chart":mode==="fundamental"?"fundamental":"portfolio";
     window.MSKDashboard?.go(target,{scroll:false});
     document.dispatchEvent(new CustomEvent("msk:import-done",{detail:{kind:mode,name:current.name}}));
     close();
   }catch(e){$("confirmImport").disabled=false;setMessage("Import failed: "+e.message,true);}
 }
 openBtn.addEventListener("click",()=>open("price"));
 $("closeImport").addEventListener("click",close);
 $("importCancel").addEventListener("click",close);
 $("importBackdrop").addEventListener("click",close);
 document.addEventListener("keydown",e=>{if(!modal.hidden&&e.key==="Escape")close();});
 tabs.forEach(t=>t.addEventListener("click",()=>select(t.dataset.importKind)));
 input.addEventListener("change",()=>prepare(input.files?.[0]));
 $("confirmImport").addEventListener("click",confirm);
 $("importTemplate").addEventListener("click",()=>{
   const blob=new Blob([templates[mode]],{type:"text/csv;charset=utf-8"});
   const url=URL.createObjectURL(blob);
   const a=document.createElement("a");a.href=url;a.download="msk-"+mode+"-template.csv";
   document.body.append(a);a.click();a.remove();
   setTimeout(()=>URL.revokeObjectURL(url),1000);
 });
 // Existing price file control is still supported, but one visible Import
 // Center is the canonical path for all modes.
 window.MSKImport={open,close,mode:()=>mode,readPreview:preview,holdings};
})();
