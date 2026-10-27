/** MSK Web Advisory — separate free Neon Functions backend, no Telegram.
 * No AI request is attempted unless explicitly enabled by the owner with
 * separate secret env variables AND a separately authenticated web identity.
 * Public anonymous visitor traffic cannot trigger provider costs.
 */
const ORIGIN="https://msk-web-advisory-preview.onrender.com";
const allowed=[ORIGIN];
const MAX_BODY=5000;
function cors(request){
 const o=request.headers.get("origin")||"";
 return allowed.includes(o)?{
   "access-control-allow-origin":o,
   "access-control-allow-methods":"GET,POST,OPTIONS",
   "access-control-allow-headers":"content-type,authorization",
   "access-control-max-age":"600",
   "vary":"Origin",
 }:{"vary":"Origin"};
}
function json(request,obj,status=200){
 return new Response(JSON.stringify(obj),{status,headers:{"content-type":"application/json; charset=utf-8","cache-control":"no-store","x-content-type-options":"nosniff",...cors(request)}});
}
const isOrigin=r=>!r.headers.has("origin")||allowed.includes(r.headers.get("origin"));
const enabled=()=>!!(process.env.AI_PUBLIC_ENABLED==="1" && (process.env.GROQ_API_KEY||process.env.GEMINI_API_KEY));
function status(){
 return {service:"msk-web-advisory",ok:true,version:"mvp-2026-10-27",
   database:"independent-neon-project",
   generative_ai:enabled()?"requires_auth":"disabled",
   historical_quotes:"imported_csv_only",
   live_market_feed:"not_configured",
   market_data_rights:"no licensed redistributable feed connected",
   pricing:"free-tier only; no AI provider call by default"};
}
const RESULT_HEADERS={"content-type":"application/json; charset=utf-8","cache-control":"no-store"};
export default {
 async fetch(request){
  const url=new URL(request.url);
  if(request.method==="OPTIONS")return new Response(null,{status:204,headers:cors(request)});
  if(!isOrigin(request))return json(request,{error:"origin_not_allowed"},403);
  if((url.pathname==="/"||url.pathname==="/health"||url.pathname==="/api/health")&&request.method==="GET")return json(request,status());
  if(url.pathname==="/api/ai/ask"&&request.method==="POST"){
    // The static frontend must never embed a reusable login/API key. Until an
    // authentication provider and abuse controls are installed, generative AI
    // intentionally rejects anonymous requests, even if env API keys exist.
    const identity=request.headers.get("authorization");
    if(!enabled()||!identity){
      return json(request,{ok:false,error:"real_ai_requires_secure_login",
        answer:"Generative AI is not enabled for anonymous visitors. Local research assistant remains available."},503);
    }
    return json(request,{ok:false,error:"auth_not_implemented",
      answer:"AI identity authorization is not implemented; model calls are disabled."},403);
  }
  if(url.pathname==="/api/sources"&&request.method==="GET"){
    return json(request,{source:"CSV import",verified:false,
      note:"No market-data redistribution license configured. Do not treat uploads as exchange-verified quotes."});
  }
  return json(request,{error:"not_found"},404);
 }
};
