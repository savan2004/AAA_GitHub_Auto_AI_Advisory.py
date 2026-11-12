"use strict";
/**
 * Dedicated pages share one audited research workspace without copying nine
 * conflicting HTML/JS implementations. A direct visit to /technical.html,
 * /portfolio.html etc. hydrates the identical site shell and loads dependencies
 * in the same order as index.html. Navigation within a session uses History API
 * so local CSV portfolios and in-memory stock research are not lost.
 */
(async()=>{
 const host=document.getElementById("mskPageLoader");
 try{
  const res=await fetch(new URL("index.html",window.location.href),{cache:"no-store",credentials:"same-origin"});
  if(!res.ok)throw Error("Website shell HTTP "+res.status);
  const html=await res.text();
  const documentShell=new DOMParser().parseFromString(html,"text/html");
  if(!documentShell.getElementById("detailBody")||!documentShell.querySelector(".primary-nav"))
    throw Error("Website shell is incomplete");
  document.body.className=documentShell.body.className;
  document.body.replaceChildren(...Array.from(documentShell.body.childNodes).map(n=>document.importNode(n,true)));
  const scripts=[...documentShell.querySelectorAll("script[src]")];
  for(const el of scripts){
    await new Promise((resolve,reject)=>{
      const script=document.createElement("script");
      script.src=new URL(el.getAttribute("src"),window.location.href).href;
      script.async=false;
      script.onload=resolve;
      script.onerror=()=>reject(Error("Unable to load "+el.getAttribute("src")));
      document.body.append(script);
    });
  }
  document.dispatchEvent(new CustomEvent("msk:route-loaded",{detail:{path:location.pathname}}));
 }catch(error){
  console.error("Dedicated research page failed to load",error);
  document.body.innerHTML="";
  const main=document.createElement("main");main.className="route-error";
  const h=document.createElement("h1");h.textContent="Research page temporarily unavailable";
  const p=document.createElement("p");p.textContent="The website could not initialize this module. Reload to retry or return to the dashboard.";
  const a=document.createElement("a");a.href="index.html";a.textContent="Back to Research Dashboard";
  main.append(h,p,a);document.body.append(main);
 }
})();
