"use strict";
/* Company-first journey: explicit evidence status, no invented fundamentals. */
(() => {
  const search=document.querySelector(".research-search");
  if(!search||document.getElementById("companyJourney"))return;
  const $=id=>document.getElementById(id);
  const names={BEL:["Bharat Electronics Limited","Defence electronics"],RELIANCE:["Reliance Industries Limited","Diversified energy and retail"],TCS:["Tata Consultancy Services Limited","IT services"],INFY:["Infosys Limited","IT services"],SBIN:["State Bank of India","Banking"]};
  const sec=document.createElement("section");
  sec.id="companyJourney";
  sec.className="company-journey";
  sec.setAttribute("aria-label","Selected company research");
  sec.innerHTML='<div class="journey-top"><div><span class="eyebrow">COMPANY INTELLIGENCE</span><h2 id="journeyName">Select a stock to research</h2><p id="journeySector">Search a NSE stock above to begin.</p></div><span id="journeySource" class="journey-source">AWAITING DATA</span></div><div class="journey-metrics"><div><small>Selected symbol</small><strong id="journeySymbol">—</strong></div><div><small>Latest sourced close</small><strong id="journeyPrice">—</strong></div><div><small>Reporting session</small><strong id="journeyDate">—</strong></div><div><small>Financial statements</small><strong id="journeyFinancials">Not supplied</strong></div></div><p id="journeyGuidance">Research actions below will use the selected company. Any missing data must remain unavailable, not guessed.</p><div class="journey-actions"><button type="button" data-journey="fundamental">Financials</button><button type="button" data-journey="technical">Technical</button><button type="button" data-journey="chart">Chart</button><button type="button" data-journey="pdf">Full Research Report</button></div>';
  const after=search.nextElementSibling;
  if(after)after.before(sec);else search.after(sec);
  function update(){
    const state=window.MSKWeb?.state;
    const code=String($("ticker")?.value||"").trim().toUpperCase();
    const known=names[code];
    $("journeyName").textContent=known?known[0]:"NSE:"+ (code||"No symbol selected")+" — company not independently verified";
    $("journeySector").textContent=known?known[1]+" • Identity reference only, not a verified financial data feed":"Company identity and sector require verification.";
    $("journeySymbol").textContent=code||"—";
    const calc=state?.calc;
    const usable=!!calc&&state?.kind!=="synthetic"&&state?.kind!=="external";
    $("journeyPrice").textContent=usable&&Number.isFinite(calc.last)?"₹"+calc.last.toLocaleString("en-IN",{maximumFractionDigits:2}):"Unavailable";
    $("journeyDate").textContent=usable?(calc.date||"Unavailable"):"Unavailable";
    const filing=window.MSKWeb?.features?.statements?.get(code);
    $("journeyFinancials").textContent=filing?(filing.asof+" • CSV"):"Not supplied";
    $("journeySource").textContent=usable?(state.kind==="tej_eod"?"HISTORICAL EOD":"USER CSV • UNVERIFIED"):(state?.kind==="synthetic"?"SYNTHETIC DEMO":"SOURCE NOT VERIFIED");
    $("journeyGuidance").textContent=usable?"Analysis uses dated historical observations. Corporate action adjustments, filings and licensing may require further verification.":"No confirmed company price or financial report available yet. Use a validated data source or import dated CSV; tools will show honest unavailable states.";
  }
  sec.querySelectorAll("[data-journey]").forEach(b=>b.addEventListener("click",()=>{
    const tab=b.dataset.journey;
    if(window.MSKDashboard?.go)window.MSKDashboard.go(tab);
    else document.querySelector('.nav[data-tab="'+tab+'"]')?.click();
  }));
  $("ticker")?.addEventListener("input",update);
  document.addEventListener("msk:stock-ready",update);
  document.addEventListener("msk:import-done",update);
  $("analyzeSymbol")?.addEventListener("click",()=>setTimeout(update,0));
  update();
})();