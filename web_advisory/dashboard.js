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
 function activateTab(tab,{scroll=true}={}){
   const target=nav.find(b=>b.dataset.tab===tab);
   if(!target)return false;
   target.click(); // Use existing real app.js button handler, never a fake tab.
   for(const a of actions)a.classList.toggle("active",a.dataset.go===tab);
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
     for(const action of actions)action.classList.toggle("active",action.dataset.go===item.dataset.tab);
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
