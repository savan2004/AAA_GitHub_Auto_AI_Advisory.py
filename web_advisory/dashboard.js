"use strict";
/* MSK Research professional interaction layer. All surfaces use the SAME
   existing stock/CSV engine. No third-party paid assets or AI calls.
*/
(() => {
 const root=document.body;
 const $=id=>document.getElementById(id);
 const nav=[...document.querySelectorAll(".nav[data-tab]")];
 const actions=[...document.querySelectorAll(".action-card[data-go]")];
 const watch=[...document.querySelectorAll(".watch-stock[data-stock]")];
 const existing=window.MSKWeb;
 if(!existing?.features){
   console.error("MSK dashboard: research engine missing; buttons cannot be activated");
   return;
 }
 function closeMenu(){
   root.classList.remove("menu-open");
   $("mobileMenuToggle")?.setAttribute("aria-expanded","false");
   document.getElementById("mobileScrim")?.remove();
 }
 function toggleMenu(){
   if(root.classList.contains("menu-open")){closeMenu();return;}
   root.classList.add("menu-open");
   $("mobileMenuToggle")?.setAttribute("aria-expanded","true");
   const scrim=document.createElement("button");
   scrim.id="mobileScrim";scrim.className="menu-scrim";scrim.type="button";
   scrim.setAttribute("aria-label","Close navigation");
   scrim.addEventListener("click",closeMenu);
   document.body.append(scrim);
 }
 $("mobileMenuToggle")?.addEventListener("click",toggleMenu);
 document.addEventListener("keydown",e=>{if(e.key==="Escape")closeMenu();});
 const VIEWS={
   overview:{title:"Research Dashboard",subtitle:"Your stock snapshot, source-dated candles and essential research tools."},
   technical:{title:"Technical Analysis",subtitle:"Evaluate EMA trends, ADX strength, volume and historical price structure."},
   fundamental:{title:"Fundamental Research",subtitle:"Work with company filings and source-dated financial metrics. Never infer earnings from candles."},
   chart:{title:"Candlestick Chart",subtitle:"Study the selected instrument with actual historical OHLCV and indicator overlays."},
   screener:{title:"Stock Screener",subtitle:"Filter the stocks actually loaded into your research universe."},
   breadth:{title:"Market Breadth",subtitle:"Compare same-session advances, declines and participation across loaded constituents."},
   pdf:{title:"Research Report",subtitle:"Inspect source-dated analysis and print an educational research sheet."},
   ai:{title:"Ask Research",subtitle:"Evidence-grounded educational Q&A; generative AI is not connected."},
   portfolio:{title:"Portfolio Health",subtitle:"Private holdings analysis from CSV; historical valuations only when matching EOD prices exist."}
 };
 function setView(tab){
   const meta=VIEWS[tab]||VIEWS.overview;
   root.dataset.page=tab;
   const title=document.querySelector(".heading-block h1");
   const sub=document.querySelector(".heading-block p");
   if(title)title.textContent=meta.title;
   if(sub)sub.textContent=meta.subtitle;
   document.title="MSK Research | "+meta.title;
 }
 let syncingLocation=false;
 function activateTab(tab,{scroll=true,updateHistory=true}={}){
   const target=nav.find(b=>b.dataset.tab===tab);
   if(!target)return false;
   if(!updateHistory)syncingLocation=true;
   try{target.click();}finally{syncingLocation=false;} // Real shared engine handler
   setView(tab);
   for(const a of actions)a.classList.toggle("active",a.dataset.go===tab);
   if(updateHistory&&window.history?.pushState&&window.location.hash!=="#"+tab)
     window.history.pushState({mskPage:tab},"","#"+tab);
   closeMenu();
   const region=tab==="ai"?$("aiPanel"):$("researchDetail");
   if(scroll&&region&&typeof region.scrollIntoView==="function")
     region.scrollIntoView({behavior:"smooth",block:"start"});
   return true;
 }
 for(const action of actions){
   action.addEventListener("click",()=>activateTab(action.dataset.go));
 }
 for(const item of nav){
   item.addEventListener("click",()=>{
     setView(item.dataset.tab);
     for(const action of actions)action.classList.toggle("active",action.dataset.go===item.dataset.tab);
     if(!syncingLocation&&window.history?.pushState&&window.location.hash!=="#"+item.dataset.tab)
       window.history.pushState({mskPage:item.dataset.tab},"","#"+item.dataset.tab);
     closeMenu();
     if(item.dataset.tab!=="overview"){
       const anchor=item.dataset.tab==="ai"?$("aiPanel"):$("researchDetail");
       if(window.innerWidth<751&&anchor?.scrollIntoView)anchor.scrollIntoView({behavior:"smooth",block:"start"});
     }
   });
 }
 function syncWatch(){
   if(!window.document?.body)return;
   const symbol=$("ticker")?.value?.trim()?.toUpperCase()||"";
   for(const item of watch)item.classList.toggle("selected",item.dataset.stock===symbol);
 }
 for(const item of watch){
   item.addEventListener("click",()=>{
     const symbol=item.dataset.stock;
     $("ticker").value=symbol;
     // features.choose from market_widget.js wraps real EOD external fetch
     // AND import/demo data. Never invent a displayed close or a signal.
     const success=existing.features.choose(symbol,false);
     syncWatch();closeMenu();
     const top=document.querySelector(".heading-block");
     if(success&&top?.scrollIntoView)top.scrollIntoView({behavior:"smooth",block:"start"});
   });
 }
 $("ticker")?.addEventListener("input",syncWatch);
 $("ticker")?.addEventListener("change",syncWatch);
 const feed=$("dataLabel"),trust=$("feedTrustLabel");
 function syncEvidence(){
   if(!window.document?.body||!feed||!trust)return;
   const mode=existing.state.kind;
   const source=feed.dataset.eodProvider;
   if(mode==="tej_eod"&&source==="TejHQ"){
     trust.textContent="HISTORICAL NSE EOD";
     trust.style.color="#0b806f";
     trust.style.borderColor="#b6e1d6";
     trust.style.background="#e9f8f3";
   } else if(mode==="synthetic"){
     trust.textContent="SYNTHETIC • DEMO";
     trust.style.color="#95602b";
     trust.style.background="#fff4dd";
   } else if(mode==="uploaded"){
     trust.textContent="USER CSV • UNVERIFIED";
     trust.style.color="#7c6230";
     trust.style.background="#fff7e6";
   } else {
     trust.textContent="DATA SOURCE CHECK PENDING";
     trust.style.color="#8b6637";
     trust.style.background="#fff7e6";
   }
   syncWatch();
 }
 if(feed){
   const observer=new MutationObserver(syncEvidence);
   observer.observe(feed,{attributes:true,childList:true,subtree:true});
   syncEvidence();
 }
 function syncFromLocation(){
   const tab=window.location.hash.replace(/^#/,"");
   activateTab(VIEWS[tab]?tab:"overview",{scroll:false,updateHistory:false});
 }
 window.addEventListener("popstate",syncFromLocation);
 window.addEventListener("hashchange",syncFromLocation);
 // Deep link supports /?symbol=SBIN#fundamental or #ai,
 // and browser Back/Forward returns to the prior research workspace.
 syncFromLocation();
 // The draft action produces a local text summary in the Ask Research panel.
 // After its existing text-generation handler, take the user where it is visible.
 $("explain")?.addEventListener("click",()=>activateTab("ai"));
 document.addEventListener("msk:stock-ready",event=>{
   if(!window.document?.body)return;
   const symbol=event.detail?.symbol||$("ticker")?.value||"";
   const history=$("chatHistory");
   if(history){
     history.replaceChildren();
     const notice=document.createElement("div");
     notice.className="bubble assistant";
     notice.textContent="Now researching "+symbol+". Market observations are historical, not real time. Ask for explanations, not guaranteed targets.";
     history.append(notice);
   }
   syncEvidence();
 });
 // Easy to identify via click-through regression. These are honest "working"
 // controls: they dispatch their actual navigation and stock data requests.
 window.MSKDashboard={
   go:activateTab,
   selectStock:s=>existing.features.choose(s,false),
   activeTab:()=>existing.state.tab,
   currentSymbol:()=>$("ticker").value,
   dataMode:()=>existing.state.kind,
   closeMenu
 };
})();
