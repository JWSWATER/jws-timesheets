function minutesBetween(start, finish) {
  if (!start || !finish) return 0;
  const [sh, sm] = start.split(":").map(Number);
  const [fh, fm] = finish.split(":").map(Number);
  let minutes = (fh * 60 + fm) - (sh * 60 + sm);
  if (minutes < 0) minutes += 1440;
  return minutes;
}

function formatMinutes(value) {
  let total = Math.round(Number(value) || 0);
  const sign = total < 0 ? "-" : "";
  total = Math.abs(total);
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  return `${sign}${hours}h ${String(minutes).padStart(2, "0")}m`;
}

function recalc() {
  let workedWeek = 0;
  let annualWeek = 0;
  let publicWeek = 0;

  document.querySelectorAll(".day-card").forEach(day => {
    let workedMinutes = 0;
    day.querySelectorAll(".entry-row").forEach(row => {
      workedMinutes += minutesBetween(
        row.querySelector(".start").value,
        row.querySelector(".finish").value
      );
    });

    workedMinutes = Math.max(
      0,
      workedMinutes - (Number(day.querySelector(".break-input").value) || 0)
    );

    const annualMinutes = Math.max(
      0,
      Math.round((Number(day.querySelector(".annual-leave-input")?.value) || 0) * 60)
    );
    const publicHolidayMinutes = day.querySelector(".public-holiday-input")?.checked
      ? Math.round((Number(day.dataset.scheduledHours) || 0) * 60)
      : 0;
    const paidMinutes = workedMinutes + annualMinutes + publicHolidayMinutes;

    day.querySelector(".day-total").textContent = formatMinutes(paidMinutes);
    workedWeek += workedMinutes;
    annualWeek += annualMinutes;
    publicWeek += publicHolidayMinutes;
  });

  const paidWeek = workedWeek + annualWeek + publicWeek;
  if (document.getElementById("workedTotal")) document.getElementById("workedTotal").textContent = formatMinutes(workedWeek);
  if (document.getElementById("annualLeaveTotal")) document.getElementById("annualLeaveTotal").textContent = formatMinutes(annualWeek);
  if (document.getElementById("publicHolidayTotal")) document.getElementById("publicHolidayTotal").textContent = formatMinutes(publicWeek);
  if (document.getElementById("weekTotal")) document.getElementById("weekTotal").textContent = formatMinutes(paidWeek);
}

function bindRow(row) {
  row.querySelectorAll("input").forEach(input => input.addEventListener("input", recalc));

  const project = row.querySelector(".project-search");
  const suggestions = row.querySelector(".project-suggestions");

  project.addEventListener("input", async () => {
    const q = encodeURIComponent(project.value);
    const data = await fetch("/api/projects?q=" + q).then(response => response.json());
    suggestions.innerHTML = data.map(item => `<button type="button" data-id="${item.id}">${item.name}</button>`).join("");
    suggestions.classList.toggle("hidden", !data.length);
    suggestions.querySelectorAll("button").forEach(button => {
      button.onclick = () => {
        project.value = button.textContent;
        project.dataset.projectId = button.dataset.id;
        suggestions.classList.add("hidden");
      };
    });
  });

  project.addEventListener("focus", () => project.dispatchEvent(new Event("input")));

  row.querySelector(".delete-row").onclick = () => {
    const list = row.parentElement;
    if (list.children.length > 1) {
      row.remove();
    } else {
      row.querySelectorAll("input").forEach(input => input.value = "");
      project.dataset.projectId = "";
      suggestions.innerHTML = "";
      suggestions.classList.add("hidden");
    }
    recalc();
  };
}

document.querySelectorAll(".entry-row").forEach(bindRow);
document.querySelectorAll(".break-input,.paid-hours-input").forEach(input => input.addEventListener("input", recalc));
document.querySelectorAll(".public-holiday-input").forEach(input => input.addEventListener("change", recalc));

document.querySelectorAll(".add-row").forEach(button => {
  button.onclick = () => {
    const day = button.closest(".day-card");
    const list = button.parentElement.querySelector(".entry-list");
    const row = list.children[0].cloneNode(true);
    row.dataset.workDate = day.dataset.date;
    row.querySelectorAll("input").forEach(input => {
      input.value = "";
      if (input.classList.contains("project-search")) input.dataset.projectId = "";
    });
    const suggestions = row.querySelector(".project-suggestions");
    suggestions.innerHTML = "";
    suggestions.classList.add("hidden");
    list.appendChild(row);
    bindRow(row);
  };
});

function payload(submit = false) {
  const form = document.getElementById("timesheetForm");
  return {
    week: form.dataset.week,
    submit,
    adminAmend: form.dataset.adminAmend === "true",
    timesheetId: form.dataset.timesheetId || null,
    days: [...document.querySelectorAll(".day-card")].map(day => ({
      date: day.dataset.date,
      breakMinutes: day.querySelector(".break-input").value,
      annualLeaveHours: day.querySelector(".annual-leave-input")?.value || "0",
      publicHoliday: !!day.querySelector(".public-holiday-input")?.checked,
      entries: [...day.querySelectorAll(".entry-row")].map(row => ({
        workDate: row.dataset.workDate || day.dataset.date,
        start: row.querySelector(".start").value,
        finish: row.querySelector(".finish").value,
        projectId: row.querySelector(".project-search").dataset.projectId || "",
        description: row.querySelector(".description").value
      }))
    }))
  };
}

async function save(submit) {
  const response = await fetch("/api/save-timesheet", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": document.querySelector('meta[name="csrf-token"]').content
    },
    body: JSON.stringify(payload(submit))
  });
  const result = await response.json();

  if (!result.ok) {
    alert(result.error || "Could not save");
    return;
  }

  const form = document.getElementById("timesheetForm");
  if (form.dataset.adminAmend === "true") {
    alert("Timesheet amendment saved.");
    location.href = result.redirect_url || ("/timesheet/" + form.dataset.timesheetId);
    return;
  }

  if (submit) {
    alert("Timesheet submitted.");
    location.reload();
    return;
  }

  // Reload after a draft save so the employee immediately sees the exact rows
  // and dates that were persisted in the database.
  alert("Draft saved.");
  location.reload();
}

const saveDraft = document.getElementById("saveDraft");
const submitTimesheet = document.getElementById("submitTimesheet");
const saveAmendment = document.getElementById("saveAmendment");
if (saveDraft) saveDraft.onclick = () => save(false);
if (submitTimesheet) submitTimesheet.onclick = () => save(true);
if (saveAmendment) saveAmendment.onclick = () => save(false);

const weekStart = document.getElementById("weekStart");
if (weekStart) weekStart.onchange = () => location.href = "/dashboard?week=" + weekStart.value;

if (window.TIMESHEET_LOCKED) {
  document.querySelectorAll("#timesheetForm input,#timesheetForm button").forEach(control => control.disabled = true);
}

recalc();
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/static/service-worker.js"));
}
