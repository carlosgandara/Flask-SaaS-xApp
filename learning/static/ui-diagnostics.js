"use strict";
(() => {
  const endpoint = document.currentScript?.dataset.reportUrl;
  const originalFetch = window.fetch.bind(window);
  let reported = 0;
  function banner(message) {
    if (!document.body) {
      document.addEventListener("DOMContentLoaded",() => banner(message),{once:true});
      return;
    }
    let panel = document.getElementById("diagnostic-banner");
    if (!panel) {
      panel = document.createElement("div");
      panel.id = "diagnostic-banner";
      panel.setAttribute("role","alert");
      Object.assign(panel.style,{position:"fixed",bottom:"12px",left:"12px",right:"12px",zIndex:"10000",padding:"14px",borderRadius:"10px",background:"#fff3d8",color:"#482b13",boxShadow:"0 3px 16px #0003",font:"14px/1.5 system-ui"});
      document.body.append(panel);
    }
    panel.textContent = message;
  }
  function report(kind, filename, line, column) {
    if (!endpoint || reported++ >= 5) return;
    let file;
    try {
      const url = new URL(filename,location.href);
      if (url.origin === location.origin && /^\/(?:learning\/)?static\//.test(url.pathname)) file = url.pathname;
    } catch (_) { /* A missing source location is still a reportable error. */ }
    banner("A page error occurred. Preparing an error reference…");
    originalFetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kind,file,line,column})})
      .then(async response => {
        if (!response.ok) throw new Error("Diagnostic reporting unavailable");
        const data = await response.json();
        banner(`A page error occurred. Reference: ${data.request_id}. Share this reference when reporting the problem.`);
      }).catch(() => banner("A page error occurred. Error reporting is unavailable; the browser console has the details."));
  }
  window.addEventListener("error",event => {
    // Script failures and failed local resource loads are captured without sending
    // page contents, answers, arbitrary error messages, or credential-bearing URLs.
    if (!event.filename && !["SCRIPT","LINK"].includes(event.target?.tagName)) return;
    report("script_error",event.filename || event.target?.src || event.target?.href,event.lineno,event.colno);
  },true);
  window.addEventListener("unhandledrejection",() => report("unhandled_rejection"));
  window.fetch = async (input, options) => {
    let local = false;
    try {local = new URL(typeof input === "string" ? input : input.url,location.href).origin === location.origin;} catch (_) {}
    try {
      const response = await originalFetch(input,options);
      if (local && response.status >= 500) {
        const reference = response.headers.get("X-Request-ID");
        banner(`The request failed. ${reference ? `Reference: ${reference}. ` : ""}Please share this reference if the problem continues.`);
      }
      return response;
    } catch (error) {
      if (local) banner("The app could not reach the server. Check the connection and try again.");
      throw error;
    }
  };
})();
