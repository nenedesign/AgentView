// Day 1 plumbing: fetch /api/sessions and render raw JSON.
// Day 2 replaces this with the proper session + activity shell.

const select = document.getElementById("fixture-select");
const output = document.getElementById("output");

async function loadFixtures() {
  const res = await fetch("/api/fixtures");
  const data = await res.json();
  select.innerHTML = "";
  for (const name of data.fixtures) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    if (name === data.default) opt.selected = true;
    select.appendChild(opt);
  }
}

async function loadSessions(fixture) {
  output.textContent = "Loading...";
  const url = fixture
    ? `/api/sessions?fixture=${encodeURIComponent(fixture)}`
    : "/api/sessions";
  const res = await fetch(url);
  if (!res.ok) {
    output.textContent = `Error ${res.status}`;
    return;
  }
  const data = await res.json();
  output.textContent = JSON.stringify(data, null, 2);
}

select.addEventListener("change", (e) => loadSessions(e.target.value));

(async () => {
  await loadFixtures();
  await loadSessions(select.value);
})();
