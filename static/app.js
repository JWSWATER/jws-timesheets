
function h(start,finish){if(!start||!finish)return 0;let[a,b]=start.split(":").map(Number),[c,d]=finish.split(":").map(Number);let m=(c*60+d)-(a*60+b);if(m<0)m+=1440;return m/60}
function recalc(){
  let workedWeek=0, annualWeek=0, publicWeek=0;
  document.querySelectorAll(".day-card").forEach(day=>{
    let worked=0;
    day.querySelectorAll(".entry-row").forEach(r=>worked+=h(r.querySelector(".start").value,r.querySelector(".finish").value));
    worked=Math.max(0,worked-(Number(day.querySelector(".break-input").value)||0)/60);
    const annual=Math.max(0,Number(day.querySelector(".annual-leave-input")?.value)||0);
    const publicHoliday=day.querySelector(".public-holiday-input")?.checked ? (Number(day.dataset.scheduledHours)||0) : 0;
    const paid=worked+annual+publicHoliday;
    day.querySelector(".day-total").textContent=paid.toFixed(2)+" hrs";
    workedWeek+=worked; annualWeek+=annual; publicWeek+=publicHoliday;
  });
  const paidWeek=workedWeek+annualWeek+publicWeek;
  if(document.getElementById("workedTotal"))document.getElementById("workedTotal").textContent=workedWeek.toFixed(2)+" hrs";
  if(document.getElementById("annualLeaveTotal"))document.getElementById("annualLeaveTotal").textContent=annualWeek.toFixed(2)+" hrs";
  if(document.getElementById("publicHolidayTotal"))document.getElementById("publicHolidayTotal").textContent=publicWeek.toFixed(2)+" hrs";
  if(document.getElementById("weekTotal"))document.getElementById("weekTotal").textContent=paidWeek.toFixed(2)+" hrs";
}
function bindRow(r){r.querySelectorAll("input").forEach(i=>i.addEventListener("input",recalc));let p=r.querySelector(".project-search"),s=r.querySelector(".project-suggestions");
p.addEventListener("input",async()=>{let q=encodeURIComponent(p.value);let data=await fetch("/api/projects?q="+q).then(x=>x.json());s.innerHTML=data.map(x=>`<button type="button" data-id="${x.id}">${x.name}</button>`).join("");s.classList.toggle("hidden",!data.length);s.querySelectorAll("button").forEach(b=>b.onclick=()=>{p.value=b.textContent;p.dataset.projectId=b.dataset.id;s.classList.add("hidden")})});
p.addEventListener("focus",()=>p.dispatchEvent(new Event("input")));r.querySelector(".delete-row").onclick=()=>{let list=r.parentElement;if(list.children.length>1)r.remove();else r.querySelectorAll("input").forEach(x=>x.value="");recalc()}}
document.querySelectorAll(".entry-row").forEach(bindRow);document.querySelectorAll(".break-input,.paid-hours-input").forEach(i=>i.addEventListener("input",recalc));document.querySelectorAll(".public-holiday-input").forEach(i=>i.addEventListener("change",recalc));document.querySelectorAll(".add-row").forEach(b=>b.onclick=()=>{let list=b.parentElement.querySelector(".entry-list"),r=list.children[0].cloneNode(true);r.querySelectorAll("input").forEach(x=>{x.value="";if(x.classList.contains("project-search"))x.dataset.projectId=""});list.appendChild(r);bindRow(r)});
function payload(submit=false){const form=document.getElementById("timesheetForm");return{
  week:form.dataset.week,
  submit,
  adminAmend:form.dataset.adminAmend==="true",
  timesheetId:form.dataset.timesheetId||null,
  days:[...document.querySelectorAll(".day-card")].map(d=>({
    date:d.dataset.date,
    breakMinutes:d.querySelector(".break-input").value,
    annualLeaveHours:d.querySelector(".annual-leave-input")?.value||"0",
    publicHoliday:!!d.querySelector(".public-holiday-input")?.checked,
    entries:[...d.querySelectorAll(".entry-row")].map(r=>({
      start:r.querySelector(".start").value,
      finish:r.querySelector(".finish").value,
      projectId:r.querySelector(".project-search").dataset.projectId||"",
      description:r.querySelector(".description").value
    }))
  }))
}}
async function save(submit){let res=await fetch("/api/save-timesheet",{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":document.querySelector('meta[name="csrf-token"]').content},body:JSON.stringify(payload(submit))});let j=await res.json();if(!j.ok)alert(j.error||"Could not save");else{const form=document.getElementById("timesheetForm");if(form.dataset.adminAmend==="true"){alert("Timesheet amendment saved.");location.href=j.redirect_url||("/timesheet/"+form.dataset.timesheetId);return;}alert(submit?"Timesheet submitted.":"Draft saved.");if(submit)location.reload()}}
let s=document.getElementById("saveDraft"),t=document.getElementById("submitTimesheet"),a=document.getElementById("saveAmendment");if(s)s.onclick=()=>save(false);if(t)t.onclick=()=>save(true);if(a)a.onclick=()=>save(false);let wk=document.getElementById("weekStart");if(wk)wk.onchange=()=>location.href="/dashboard?week="+wk.value;
if(window.TIMESHEET_LOCKED){document.querySelectorAll("#timesheetForm input,#timesheetForm button").forEach(x=>x.disabled=true)}
recalc();
if("serviceWorker" in navigator)window.addEventListener("load",()=>navigator.serviceWorker.register("/static/service-worker.js"));
